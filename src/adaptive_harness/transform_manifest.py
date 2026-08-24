"""Bounded static manifests for public task-provided Python transforms."""

from __future__ import annotations

import ast
import hashlib
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class PathAccessKind(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class StaticPathAccess:
    kind: PathAccessKind
    path: str
    exists: bool
    inside_workspace: bool
    safe: bool
    line: int

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "exists": self.exists,
            "inside_workspace": self.inside_workspace,
            "safe": self.safe,
            "line": self.line,
        }


@dataclass(frozen=True)
class TransformManifest:
    script_path: str
    sha256: str
    bytes: int
    accesses: tuple[StaticPathAccess, ...]
    unresolved_access_count: int

    @property
    def missing_reads(self) -> tuple[StaticPathAccess, ...]:
        return tuple(
            access
            for access in self.accesses
            if access.kind is PathAccessKind.READ and access.safe and not access.exists
        )

    @property
    def unsafe_accesses(self) -> tuple[StaticPathAccess, ...]:
        return tuple(access for access in self.accesses if not access.safe)

    def to_payload(self) -> dict[str, object]:
        return {
            "script_path": self.script_path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "accesses": [access.to_payload() for access in self.accesses],
            "unresolved_access_count": self.unresolved_access_count,
            "unsafe_access_count": len(self.unsafe_accesses),
        }


@dataclass(frozen=True)
class WorkspaceTransformScan:
    manifests: tuple[TransformManifest, ...]
    scanned_scripts: int
    skipped_scripts: int
    visited_paths: int
    truncated: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "manifests": [manifest.to_payload() for manifest in self.manifests],
            "scanned_scripts": self.scanned_scripts,
            "skipped_scripts": self.skipped_scripts,
            "visited_paths": self.visited_paths,
            "truncated": self.truncated,
        }


class PythonTransformManifestScanner:
    """Statically resolve simple Path-based transform dependencies without execution."""

    def __init__(
        self,
        *,
        max_scripts: int = 32,
        max_script_bytes: int = 256 * 1024,
        max_visited_paths: int = 4096,
    ) -> None:
        if max_scripts < 1 or max_script_bytes < 1 or max_visited_paths < 1:
            raise ValueError("transform manifest bounds must be positive")
        self.max_scripts = max_scripts
        self.max_script_bytes = max_script_bytes
        self.max_visited_paths = max_visited_paths

    def scan(self, task_root: Path) -> WorkspaceTransformScan:
        root = task_root.resolve()
        workspace = (root / "workspace").resolve()
        if not workspace.is_dir() or not workspace.is_relative_to(root):
            return WorkspaceTransformScan((), 0, 0, 0, False)
        scripts, visited, traversal_truncated = self._python_scripts(workspace)
        if traversal_truncated and not scripts:
            return WorkspaceTransformScan((), 0, 0, visited, True)
        manifests = []
        skipped = 0
        truncated = traversal_truncated
        for script in scripts:
            try:
                relative = script.relative_to(workspace)
                if script.is_symlink() or any(part.startswith(".") for part in relative.parts):
                    skipped += 1
                    continue
                resolved = script.resolve(strict=True)
                if not resolved.is_relative_to(workspace) or not resolved.is_file():
                    skipped += 1
                    continue
                size = resolved.stat().st_size
                if size > self.max_script_bytes:
                    skipped += 1
                    continue
                source = resolved.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(relative))
            except (OSError, UnicodeDecodeError, SyntaxError):
                skipped += 1
                continue
            accesses, unresolved = _analyze_python_paths(
                tree,
                script=resolved,
                task_root=root,
                workspace=workspace,
            )
            manifests.append(
                TransformManifest(
                    script_path=f"workspace/{relative.as_posix()}",
                    sha256=hashlib.sha256(source.encode()).hexdigest(),
                    bytes=size,
                    accesses=accesses,
                    unresolved_access_count=unresolved,
                )
            )
        return WorkspaceTransformScan(
            tuple(manifests),
            len(manifests),
            skipped,
            visited,
            truncated,
        )

    def _python_scripts(self, workspace: Path) -> tuple[tuple[Path, ...], int, bool]:
        stack = [workspace]
        scripts: list[Path] = []
        visited = 0
        while stack:
            directory = stack.pop()
            try:
                entries = []
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        visited += 1
                        if visited > self.max_visited_paths:
                            return (), self.max_visited_paths, True
                        entries.append(entry)
            except OSError:
                continue
            child_directories = []
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child_directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".py"):
                        scripts.append(Path(entry.path))
                        if len(scripts) > self.max_scripts:
                            return tuple(scripts[: self.max_scripts]), visited, True
                except OSError:
                    continue
            stack.extend(reversed(child_directories))
        return tuple(scripts), visited, False


class _StaticPathAnalyzer(ast.NodeVisitor):
    def __init__(self, *, script: Path, task_root: Path, workspace: Path) -> None:
        self.script = script
        self.task_root = task_root
        self.workspace = workspace
        self.symbols: dict[str, Path] = {}
        self.accesses: list[StaticPathAccess] = []
        self.unresolved = 0

    def visit_Assign(self, node: ast.Assign) -> Any:  # noqa: N802 - ast visitor API.
        value = self._path(node.value)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.symbols[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:  # noqa: N802 - ast visitor API.
        value = self._path(node.value) if node.value is not None else None
        if value is not None and isinstance(node.target, ast.Name):
            self.symbols[node.target.id] = value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:  # noqa: N802 - ast visitor API.
        kind: PathAccessKind | None = None
        path_node: ast.AST | None = None
        unknown_access = False
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in {"read_text", "read_bytes"}:
                kind = PathAccessKind.READ
                path_node = node.func.value
            elif node.func.attr in {"write_text", "write_bytes"}:
                kind = PathAccessKind.WRITE
                path_node = node.func.value
            elif node.func.attr == "open":
                path_node = node.func.value
                kind = _open_kind(node)
                unknown_access = kind is None
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
            path_node = node.args[0]
            kind = _open_kind(node)
            unknown_access = kind is None
        if unknown_access:
            self.unresolved += 1
            path_node = None
        if kind is not None and path_node is not None:
            path = self._path(path_node)
            if path is None:
                self.unresolved += 1
            else:
                self.accesses.append(self._access(kind, path, node.lineno))
        self.generic_visit(node)

    def _access(self, kind: PathAccessKind, path: Path, line: int) -> StaticPathAccess:
        absolute = path if path.is_absolute() else self.task_root / path
        normalized = Path(os.path.normpath(str(absolute)))
        safe = _safe_public_access_path(
            normalized,
            task_root=self.task_root,
            workspace=self.workspace,
        )
        if not safe:
            display = "<restricted-path>"
        elif normalized.is_relative_to(self.task_root):
            display = normalized.relative_to(self.task_root).as_posix()
        else:
            display = "<outside-task>"
        return StaticPathAccess(
            kind=kind,
            path=display,
            exists=normalized.exists() if safe else False,
            inside_workspace=normalized.is_relative_to(self.workspace),
            safe=safe,
            line=line,
        )

    def _path(self, node: ast.AST) -> Path | None:
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return self.script
            return self.symbols.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return Path(node.value)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Path" and node.args:
                return self._path(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
                return self._path(node.func.value)
            return None
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            value = self._path(node.value)
            return value.parent if value is not None else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._path(node.left)
            right = _literal_string(node.right)
            return left / right if left is not None and right is not None else None
        return None


def _literal_string(node: ast.AST) -> str | None:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _open_kind(node: ast.Call) -> PathAccessKind | None:
    mode: str | None = None
    mode_provided = False
    if len(node.args) >= 2:
        mode_provided = True
        mode = _literal_string(node.args[1])
    if not mode_provided:
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode_provided = True
                mode = _literal_string(keyword.value)
                break
    if mode_provided and mode is None:
        return None
    return (
        PathAccessKind.WRITE
        if mode is not None and any(marker in mode for marker in ("w", "a", "x", "+"))
        else PathAccessKind.READ
    )


def _safe_public_access_path(path: Path, *, task_root: Path, workspace: Path) -> bool:
    if not path.is_relative_to(task_root):
        return False
    relative = path.relative_to(task_root)
    forbidden = {"private", "verifier", "reward", "grader", "rubric"}
    if any(part.startswith(".") or part.lower() in forbidden for part in relative.parts):
        return False
    current = task_root
    for part in relative.parts:
        current /= part
        try:
            if current.is_symlink():
                return False
        except OSError:
            return False
    return path.is_relative_to(workspace) or not path.exists()


def _analyze_python_paths(
    tree: ast.AST,
    *,
    script: Path,
    task_root: Path,
    workspace: Path,
) -> tuple[tuple[StaticPathAccess, ...], int]:
    analyzer = _StaticPathAnalyzer(
        script=script,
        task_root=task_root,
        workspace=workspace,
    )
    analyzer.visit(tree)
    unique = {
        (access.kind, access.path, access.line): access
        for access in analyzer.accesses
    }
    ordered = sorted(unique, key=lambda item: (item[2], item[0], item[1]))
    return tuple(unique[key] for key in ordered), analyzer.unresolved
