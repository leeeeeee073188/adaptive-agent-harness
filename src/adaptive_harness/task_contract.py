"""Task contracts derived only from model-visible public task inputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol


class CriterionKind(StrEnum):
    ARTIFACT_EXISTS = "artifact_exists"
    EXACT_COUNT = "exact_count"
    DEPENDENCY = "dependency"
    OBSERVATION_EQUALS = "observation_equals"


class CriterionSource(StrEnum):
    TASK_PROMPT = "task_prompt"
    PUBLIC_SCHEMA = "public_schema"
    RUNTIME_OBSERVATION = "runtime_observation"


@dataclass(frozen=True)
class Criterion:
    id: str
    description: str
    kind: CriterionKind
    source: CriterionSource
    parameters: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    required: bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "kind": self.kind.value,
            "source": self.source.value,
            "parameters": dict(self.parameters),
            "depends_on": list(self.depends_on),
            "required": self.required,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Criterion:
        return cls(
            id=str(payload["id"]),
            description=str(payload["description"]),
            kind=CriterionKind(payload["kind"]),
            source=CriterionSource(payload["source"]),
            parameters=dict(payload.get("parameters") or {}),
            depends_on=tuple(str(item) for item in payload.get("depends_on") or ()),
            required=bool(payload.get("required", True)),
        )


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    original_request: str
    criteria: tuple[Criterion, ...]
    public_schema_hash: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "original_request": self.original_request,
            "criteria": [criterion.to_payload() for criterion in self.criteria],
            "public_schema_hash": self.public_schema_hash,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskContract:
        return cls(
            task_id=str(payload["task_id"]),
            original_request=str(payload["original_request"]),
            criteria=tuple(Criterion.from_payload(item) for item in payload.get("criteria") or ()),
            public_schema_hash=(
                str(payload["public_schema_hash"])
                if payload.get("public_schema_hash") is not None
                else None
            ),
        )


class ContractBuilder(Protocol):
    def build(
        self,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, Any] | None = None,
    ) -> TaskContract: ...


class RuleBasedTaskContractBuilder:
    """Conservative parser; ambiguity stays unverified rather than invented."""

    _ARTIFACT = re.compile(r"(?<![\w/])(?:/task/)?outputs/[A-Za-z0-9_.\-/]+")
    _EXACT_COUNT = re.compile(
        r"\bexactly\s+(?P<count>\d+)\s+(?P<subject>[A-Za-z][A-Za-z0-9_-]*(?:\s+[A-Za-z][A-Za-z0-9_-]*){0,2})",
        re.IGNORECASE,
    )
    _EXACT_COUNT_WORD = re.compile(
        r"\bexactly\s+(?P<count>one|two|three|four|five|six|seven|eight|nine|ten)\s+(?P<subject>[A-Za-z][A-Za-z0-9_-]*)",
        re.IGNORECASE,
    )
    _CODE_FILE = re.compile(r"`(?P<path>[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]{1,10})`")
    _NUMBER_WORDS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    _STATE_RULES = (
        (
            "listing.submitted",
            True,
            "Listing submission is observed",
            (r"发上线|publish|submit", r"发品系统|listing|product|商品"),
        ),
        (
            "mail.label_created",
            True,
            "Required mail label is observed",
            (r"创建(?:顶层)?标签|create (?:a )?(?:top-level )?label",),
        ),
        (
            "mail.draft_saved",
            True,
            "Required unsent draft is observed",
            (r"未发送草稿|unsent draft|save (?:an? )?draft",),
        ),
        (
            "calendar.event_created",
            True,
            "Required calendar event is observed",
            (r"日历事件|calendar event", r"再建|创建|create|schedule"),
        ),
        (
            "document.updated",
            True,
            "Target document update is observed",
            (r"document|文档", r"apply it|update|更新|修改"),
        ),
    )
    _FORBIDDEN_SCHEMA_KEYS = {
        "expected_answer",
        "ground_truth",
        "groundtruth",
        "rubric",
        "verifier",
        "judge",
    }

    def build(
        self,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, Any] | None = None,
    ) -> TaskContract:
        if not task_id.strip() or not task_prompt.strip():
            raise ValueError("task_id and task_prompt must be non-empty")
        schema = dict(public_schema or {})
        self._reject_evaluation_data(schema)
        criteria: list[Criterion] = []
        used_ids: set[str] = set()

        for match in self._ARTIFACT.finditer(task_prompt):
            path = _normalize_path(match.group(0).rstrip(".,;:)]}"))
            if any(item.kind is CriterionKind.ARTIFACT_EXISTS and item.parameters["path"] == path for item in criteria):
                continue
            criteria.append(
                Criterion(
                    id=_unique_id("artifact", path, used_ids),
                    description=f"Required artifact exists: {path}",
                    kind=CriterionKind.ARTIFACT_EXISTS,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"path": path},
                )
            )

        for path in self._output_directory_files(task_prompt):
            if any(
                item.kind is CriterionKind.ARTIFACT_EXISTS and item.parameters["path"] == path
                for item in criteria
            ):
                continue
            criteria.append(
                Criterion(
                    id=_unique_id("artifact", path, used_ids),
                    description=f"Required artifact exists: {path}",
                    kind=CriterionKind.ARTIFACT_EXISTS,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"path": path},
                )
            )

        for match in self._EXACT_COUNT.finditer(task_prompt):
            subject = " ".join(match.group("subject").lower().split())
            if subject.split()[0] in {"a", "an", "of", "the"}:
                continue
            expected = int(match.group("count"))
            criteria.append(
                Criterion(
                    id=_unique_id("count", subject, used_ids),
                    description=f"Exactly {expected} {subject}",
                    kind=CriterionKind.EXACT_COUNT,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"subject": subject, "expected": expected},
                )
            )

        for match in self._EXACT_COUNT_WORD.finditer(task_prompt):
            subject = match.group("subject").lower()
            expected = self._NUMBER_WORDS[match.group("count").lower()]
            if any(
                item.kind is CriterionKind.EXACT_COUNT
                and item.parameters.get("subject") == subject
                and item.parameters.get("expected") == expected
                for item in criteria
            ):
                continue
            criteria.append(
                Criterion(
                    id=_unique_id("count", subject, used_ids),
                    description=f"Exactly {expected} {subject}",
                    kind=CriterionKind.EXACT_COUNT,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"subject": subject, "expected": expected},
                )
            )

        for subject, expected, description, pattern_group in self._STATE_RULES:
            if not all(re.search(pattern, task_prompt, re.IGNORECASE) for pattern in pattern_group):
                continue
            criteria.append(
                Criterion(
                    id=_unique_id("observation", subject, used_ids),
                    description=description,
                    kind=CriterionKind.OBSERVATION_EQUALS,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"subject": subject, "expected": expected},
                )
            )

        criteria.extend(self._schema_criteria(schema, used_ids))
        criteria = self._apply_dependencies(criteria, schema.get("dependencies"))
        self._validate_parameters(criteria)
        self._validate_graph(criteria)
        schema_hash = _canonical_hash(schema) if public_schema is not None else None
        return TaskContract(task_id, task_prompt, tuple(criteria), schema_hash)

    def _output_directory_files(self, task_prompt: str) -> tuple[str, ...]:
        """Extract code-quoted files only near an explicit outputs/ declaration."""

        files: list[str] = []
        directory_pattern = r"`?outputs/`?(?=\s|下|内|中|[:：])"
        for match in re.finditer(directory_pattern, task_prompt, re.IGNORECASE):
            tail = task_prompt[match.start() : match.start() + 3500]
            heading = re.search(r"\n#{1,3}\s", tail[1:])
            segment = tail[: heading.start() + 1] if heading else tail
            nearby = task_prompt[max(0, match.start() - 100) : match.end() + 100]
            count_match = self._EXACT_COUNT.search(nearby) or self._EXACT_COUNT_WORD.search(nearby)
            declared_count = None
            if count_match:
                raw_count = count_match.group("count").lower()
                declared_count = int(raw_count) if raw_count.isdigit() else self._NUMBER_WORDS[raw_count]
            for code_match in self._CODE_FILE.finditer(segment):
                raw = code_match.group("path").replace("\\", "/")
                if raw.startswith(("workspace/", "http://", "https://", "/")):
                    continue
                path = _normalize_path(raw if raw.startswith("outputs/") else f"outputs/{raw}")
                if path not in files:
                    files.append(path)
                if declared_count is not None and len(files) >= declared_count:
                    break
        return tuple(files)

    def _schema_criteria(
        self,
        schema: Mapping[str, Any],
        used_ids: set[str],
    ) -> list[Criterion]:
        criteria: list[Criterion] = []
        artifacts = schema.get("artifacts") or []
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
            raise ValueError("public_schema.artifacts must be a list")
        for raw in artifacts:
            if isinstance(raw, str):
                item = {"path": raw}
            elif isinstance(raw, Mapping):
                item = dict(raw)
            else:
                raise ValueError("public_schema artifacts must be strings or objects")
            if "path" not in item:
                raise ValueError("artifact criteria require a path")
            path = _normalize_path(str(item["path"]))
            if item.get("id"):
                criterion_id = str(item["id"])
                _reserve_id(criterion_id, used_ids)
            else:
                criterion_id = _unique_id("artifact", path, used_ids)
            criteria.append(
                Criterion(
                    id=criterion_id,
                    description=str(item.get("description") or f"Required artifact exists: {path}"),
                    kind=CriterionKind.ARTIFACT_EXISTS,
                    source=CriterionSource.PUBLIC_SCHEMA,
                    parameters={"path": path},
                    depends_on=_dependencies(item.get("depends_on"), f"artifact {criterion_id!r}"),
                    required=bool(item.get("required", True)),
                )
            )

        exact_counts = schema.get("exact_counts") or {}
        if not isinstance(exact_counts, Mapping):
            raise ValueError("public_schema.exact_counts must be an object")
        for subject, expected in exact_counts.items():
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise ValueError("exact count values must be non-negative integers")
            normalized = str(subject).lower().strip()
            criteria.append(
                Criterion(
                    id=_unique_id("count", normalized, used_ids),
                    description=f"Exactly {expected} {normalized}",
                    kind=CriterionKind.EXACT_COUNT,
                    source=CriterionSource.PUBLIC_SCHEMA,
                    parameters={"subject": normalized, "expected": expected},
                )
            )

        raw_criteria = schema.get("criteria") or []
        if not isinstance(raw_criteria, Sequence) or isinstance(raw_criteria, (str, bytes)):
            raise ValueError("public_schema.criteria must be a list")
        for raw in raw_criteria:
            if not isinstance(raw, Mapping):
                raise ValueError("public_schema criteria must be objects")
            kind = CriterionKind(str(raw["kind"]))
            parameters = dict(raw.get("parameters") or {})
            if raw.get("id"):
                criterion_id = str(raw["id"])
                _reserve_id(criterion_id, used_ids)
            else:
                criterion_id = _unique_id(kind.value, json.dumps(parameters), used_ids)
            criteria.append(
                Criterion(
                    id=criterion_id,
                    description=str(raw.get("description") or criterion_id),
                    kind=kind,
                    source=CriterionSource.PUBLIC_SCHEMA,
                    parameters=parameters,
                    depends_on=_dependencies(raw.get("depends_on"), f"criterion {criterion_id!r}"),
                    required=bool(raw.get("required", True)),
                )
            )
        return criteria

    def _apply_dependencies(self, criteria: list[Criterion], raw: Any) -> list[Criterion]:
        if raw is None:
            return criteria
        if not isinstance(raw, Mapping):
            raise ValueError("public_schema.dependencies must be an object")
        dependencies: dict[str, tuple[str, ...]] = {}
        for key, value in raw.items():
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                raise ValueError("criterion dependencies must be lists")
            dependencies[str(key)] = tuple(str(dep) for dep in value)
        return [
            replace(item, depends_on=tuple(dict.fromkeys((*item.depends_on, *dependencies.get(item.id, ())))))
            for item in criteria
        ]

    def _validate_parameters(self, criteria: list[Criterion]) -> None:
        for item in criteria:
            parameters = item.parameters
            if item.kind is CriterionKind.ARTIFACT_EXISTS and not str(parameters.get("path") or "").strip():
                raise ValueError(f"artifact criterion {item.id!r} requires parameters.path")
            if item.kind is CriterionKind.EXACT_COUNT:
                expected = parameters.get("expected")
                if not str(parameters.get("subject") or "").strip():
                    raise ValueError(f"count criterion {item.id!r} requires parameters.subject")
                if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                    raise ValueError(f"count criterion {item.id!r} requires a non-negative integer expected")
            if item.kind is CriterionKind.DEPENDENCY and not item.depends_on:
                raise ValueError(f"dependency criterion {item.id!r} requires depends_on")
            if item.kind is CriterionKind.OBSERVATION_EQUALS:
                if not str(parameters.get("subject") or "").strip() or "expected" not in parameters:
                    raise ValueError(
                        f"observation criterion {item.id!r} requires parameters.subject and expected"
                    )

    def _validate_graph(self, criteria: list[Criterion]) -> None:
        ids = {item.id for item in criteria}
        for item in criteria:
            missing = set(item.depends_on) - ids
            if missing:
                raise ValueError(f"criterion {item.id!r} has unknown dependencies: {sorted(missing)}")

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {item.id: item for item in criteria}

        def visit(criterion_id: str) -> None:
            if criterion_id in visiting:
                raise ValueError("criterion dependency graph contains a cycle")
            if criterion_id in visited:
                return
            visiting.add(criterion_id)
            for dependency in by_id[criterion_id].depends_on:
                visit(dependency)
            visiting.remove(criterion_id)
            visited.add(criterion_id)

        for criterion_id in by_id:
            visit(criterion_id)

    def _reject_evaluation_data(self, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_").replace(" ", "_")
                if normalized in self._FORBIDDEN_SCHEMA_KEYS:
                    raise ValueError(f"evaluation-only field is forbidden in public_schema: {key!r}")
                self._reject_evaluation_data(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                self._reject_evaluation_data(item)


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    return normalized.removeprefix("/task/")


def _unique_id(prefix: str, value: str, used: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:48] or "criterion"
    base = f"{prefix}:{slug}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _reserve_id(criterion_id: str, used: set[str]) -> None:
    if criterion_id in used:
        raise ValueError(f"duplicate criterion id: {criterion_id!r}")
    used.add(criterion_id)


def _dependencies(raw: Any, label: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{label} depends_on must be a list")
    return tuple(str(item) for item in raw)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
