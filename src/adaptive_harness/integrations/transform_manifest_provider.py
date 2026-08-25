"""Bounded DeerFlow Evidence provider for public static Transform Manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adaptive_harness.task_contract import CriterionKind, TaskContract
from adaptive_harness.task_state import Evidence, EvidenceKind, EvidenceSource
from adaptive_harness.transform_manifest import (
    PathAccessKind,
    PythonTransformManifestScanner,
    StaticPathAccess,
    TransformManifest,
)


class TransformManifestObservationProvider:
    """Expose only relevant, bounded precondition gaps; never execute transforms."""

    def __init__(
        self,
        task_root: Path,
        *,
        scanner: PythonTransformManifestScanner | None = None,
        max_evidence: int = 8,
        max_accesses_per_kind: int = 4,
        max_payload_chars: int = 4000,
    ) -> None:
        if max_evidence < 1 or max_accesses_per_kind < 1 or max_payload_chars < 1:
            raise ValueError("transform evidence bounds must be positive")
        self.task_root = task_root.resolve()
        self.scanner = scanner or PythonTransformManifestScanner()
        self.max_evidence = max_evidence
        self.max_accesses_per_kind = max_accesses_per_kind
        self.max_payload_chars = max_payload_chars

    def supports(self, _criterion: Any) -> bool:
        return False

    def before_run(self, contract: TaskContract) -> Sequence[Evidence]:
        scan = self.scanner.scan(self.task_root)
        required_paths = tuple(
            str(criterion.parameters.get("path") or "").removeprefix("/task/")
            for criterion in contract.criteria
            if criterion.kind is CriterionKind.ARTIFACT_EXISTS
        )
        evidence = []
        used_chars = 0
        for manifest in scan.manifests:
            if len(evidence) >= self.max_evidence:
                break
            if not _relevant(manifest, contract.original_request, required_paths):
                continue
            if not (
                manifest.missing_reads
                or manifest.unsafe_accesses
                or manifest.unresolved_access_count
            ):
                continue
            value = self._value(manifest)
            encoded_chars = len(json.dumps(value, ensure_ascii=False, sort_keys=True))
            if used_chars + encoded_chars > self.max_payload_chars:
                break
            used_chars += encoded_chars
            evidence.append(
                Evidence(
                    id=(
                        "transform-manifest:"
                        + hashlib.sha256(manifest.script_path.encode()).hexdigest()[:16]
                    ),
                    kind=EvidenceKind.OBSERVATION,
                    subject=f"workspace.transform_manifest:{manifest.sha256[:16]}",
                    value=value,
                    source=EvidenceSource.RUNTIME_OBSERVATION,
                    metadata={
                        "provider": "static-transform-manifest",
                        "execution_performed": False,
                        "scan_truncated": scan.truncated,
                    },
                )
            )
        return tuple(evidence)

    def observe(
        self,
        _contract: TaskContract,
        _summary: Any,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        return ()

    def _value(self, manifest: TransformManifest) -> dict[str, object]:
        missing = manifest.missing_reads[: self.max_accesses_per_kind]
        writes = tuple(
            access
            for access in manifest.accesses
            if access.kind is PathAccessKind.WRITE and access.safe
        )[: self.max_accesses_per_kind]
        return {
            "script_path": manifest.script_path,
            "script_sha256": manifest.sha256,
            "missing_reads": [_access_payload(access) for access in missing],
            "declared_writes": [_access_payload(access) for access in writes],
            "unsafe_access_count": len(manifest.unsafe_accesses),
            "unresolved_access_count": manifest.unresolved_access_count,
            "accesses_truncated": (
                len(manifest.missing_reads) > len(missing)
                or sum(
                    access.kind is PathAccessKind.WRITE and access.safe
                    for access in manifest.accesses
                )
                > len(writes)
            ),
        }


def _access_payload(access: StaticPathAccess) -> dict[str, object]:
    return {
        "path": access.path,
        "exists": access.exists,
        "line": access.line,
    }


def _relevant(
    manifest: TransformManifest,
    task_prompt: str,
    required_paths: tuple[str, ...],
) -> bool:
    prompt = task_prompt.lower()
    script_path = manifest.script_path.lower()
    if script_path in prompt or Path(script_path).name in prompt:
        return True
    write_paths = tuple(
        access.path
        for access in manifest.accesses
        if access.kind is PathAccessKind.WRITE and access.safe
    )
    return any(
        _matches_required(write_path, required_path)
        for write_path in write_paths
        for required_path in required_paths
    )


def _matches_required(path: str, required: str) -> bool:
    return (
        path.startswith(required.rstrip("/") + "/")
        if required.endswith("/")
        else path == required
    )
