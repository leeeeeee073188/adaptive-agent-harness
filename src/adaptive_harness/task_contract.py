"""Task contracts derived only from model-visible public task inputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


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


@dataclass(frozen=True)
class CriterionDraft:
    """Provider-neutral criterion proposed by an integration extractor.

    The core owns identifiers and validation so extractors cannot smuggle an
    evaluator-specific contract around the public-schema boundary.
    """

    identity: str
    description: str
    kind: CriterionKind
    parameters: Mapping[str, Any]
    depends_on: tuple[str, ...] = ()
    required: bool = True
    source: CriterionSource = CriterionSource.TASK_PROMPT


class CriterionExtractor(Protocol):
    def extract(self, task_prompt: str) -> Sequence[CriterionDraft]: ...


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
    _JSON_FENCE = re.compile(r"```json\s*(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
    _HTTP_URL = re.compile(r"https?://[^\s`<>()\[\]{}\"']+", re.IGNORECASE)
    _ABSOLUTE_ENDPOINT = re.compile(
        r"`(?P<endpoint>/[A-Za-z0-9_.~!$&()*+,;=:@%/-]+"
        r"(?:\?[A-Za-z0-9_.~!$&()*+,;=:@%/?-]+)?)`"
    )
    _SOURCE_ACTION = re.compile(
        r"(?i)\b(?:open|access|use|visit|read|verify)\b|(?:真值|访问|打开|使用|核验)"
    )
    _STRONG_COMPLETENESS = re.compile(
        r"(?is)(?:\b(?:all|every)\b.{0,80}"
        r"\b(?:matching|qualifying|eligible|applicable|that\s+(?:match|meet)|which\s+(?:match|meet))\b"
        r"|(?:所有|全部).{0,40}(?:符合|满足|命中))"
    )
    _EMPTY_COLLECTION_ALLOWED = re.compile(
        r"(?is)(?:\b(?:may|can|could)\s+be\s+empty\b"
        r"|\b(?:zero|no)\s+(?:matches|records|items)\s+(?:is|are)\s+(?:valid|allowed|acceptable)\b"
        r"|(?:可以|允许|可)为空|(?:没有|无)(?:匹配|命中).{0,20}(?:为空|空列表))"
    )
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
    _FORBIDDEN_SCHEMA_KEYS = {
        "expected_answer",
        "ground_truth",
        "groundtruth",
        "rubric",
        "verifier",
        "judge",
    }

    def __init__(self, *, extractors: Sequence[CriterionExtractor] = ()) -> None:
        self.extractors = tuple(extractors)

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
            if (
                subject.split()[0] in {"a", "an", "of", "the"}
                or task_prompt[match.end() :].lstrip().lower().startswith("per ")
            ):
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
            if (
                subject in {"a", "an", "of", "the"}
                or task_prompt[match.end() :].lstrip().lower().startswith("per ")
            ):
                continue
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

        criteria.extend(self._json_artifact_shape_criteria(task_prompt, criteria, used_ids))
        criteria.extend(self._public_source_access_criteria(task_prompt, used_ids))

        for extractor in self.extractors:
            for draft in extractor.extract(task_prompt):
                criteria.append(
                    Criterion(
                        id=_unique_id(draft.kind.value, draft.identity, used_ids),
                        description=draft.description,
                        kind=draft.kind,
                        source=draft.source,
                        parameters=dict(draft.parameters),
                        depends_on=draft.depends_on,
                        required=draft.required,
                    )
                )

        criteria.extend(self._schema_criteria(schema, used_ids))
        criteria = self._apply_dependencies(criteria, schema.get("dependencies"))
        self._validate_parameters(criteria)
        self._validate_graph(criteria)
        schema_hash = _canonical_hash(schema) if public_schema is not None else None
        return TaskContract(task_id, task_prompt, tuple(criteria), schema_hash)

    def _json_artifact_shape_criteria(
        self,
        task_prompt: str,
        criteria: Sequence[Criterion],
        used_ids: set[str],
    ) -> list[Criterion]:
        json_artifacts = [
            item
            for item in criteria
            if item.kind is CriterionKind.ARTIFACT_EXISTS
            and str(item.parameters.get("path") or "").lower().endswith(".json")
        ]
        if len(json_artifacts) != 1:
            return []
        examples: list[Any] = []
        for match in self._JSON_FENCE.finditer(task_prompt):
            try:
                example = json.loads(match.group("body"))
            except json.JSONDecodeError:
                continue
            if not isinstance(example, (Mapping, list)):
                continue
            self._reject_evaluation_data(example)
            examples.append(example)
        if len(examples) != 1 or not isinstance(examples[0], Mapping):
            return []

        artifact = json_artifacts[0]
        path = str(artifact.parameters["path"])
        shape = _json_shape_from_example(examples[0])
        parameters: dict[str, Any] = {
            "subject": f"artifact.json_shape:{path}",
            "expected": True,
            "path": path,
            "required_top_level_keys": list(examples[0].keys()),
            "shape": shape,
            "list_identity_keys": _json_list_identity_keys(shape),
        }
        normalized_prompt = re.sub(r"[*_`]", "", task_prompt)
        non_vacuous_paths = _top_level_non_empty_collection_paths(examples[0])
        if (
            non_vacuous_paths
            and self._STRONG_COMPLETENESS.search(normalized_prompt)
            and not self._EMPTY_COLLECTION_ALLOWED.search(normalized_prompt)
        ):
            parameters["non_vacuous_collection_paths"] = non_vacuous_paths
        return [
            Criterion(
                id=_unique_id("observation", f"artifact-json-shape-{path}", used_ids),
                description=f"JSON artifact matches public example shape and completeness constraints: {path}",
                kind=CriterionKind.OBSERVATION_EQUALS,
                source=CriterionSource.TASK_PROMPT,
                parameters=parameters,
                depends_on=(artifact.id,),
            )
        ]

    def _output_directory_files(self, task_prompt: str) -> tuple[str, ...]:
        """Extract code-quoted files only near an explicit outputs/ declaration."""

        files: list[str] = []
        directory_pattern = r"`?outputs/`?(?=\s|下|内|中|[:：])"
        for match in re.finditer(directory_pattern, task_prompt, re.IGNORECASE):
            tail = task_prompt[match.start() : match.start() + 3500]
            nearby = task_prompt[max(0, match.start() - 100) : match.end() + 100]
            count_match = self._EXACT_COUNT.search(nearby) or self._EXACT_COUNT_WORD.search(nearby)
            declared_count = None
            if count_match:
                raw_count = count_match.group("count").lower()
                declared_count = int(raw_count) if raw_count.isdigit() else self._NUMBER_WORDS[raw_count]
            started = False
            for line in tail.splitlines():
                line_matches = list(self._CODE_FILE.finditer(line))
                has_explicit_output = any(
                    code_match.group("path").replace("\\", "/").startswith("outputs/")
                    for code_match in line_matches
                )
                if started and not line.strip():
                    break
                for code_match in line_matches:
                    raw = code_match.group("path").replace("\\", "/")
                    if has_explicit_output and not raw.startswith("outputs/"):
                        continue
                    if raw.startswith(("workspace/", "http://", "https://", "/")):
                        continue
                    path = _normalize_path(raw if raw.startswith("outputs/") else f"outputs/{raw}")
                    if path not in files:
                        files.append(path)
                        started = True
                    if declared_count is not None and len(files) >= declared_count:
                        break
                if declared_count is not None and len(files) >= declared_count:
                    break
        return tuple(files)

    def _public_source_access_criteria(
        self,
        task_prompt: str,
        used_ids: set[str],
    ) -> list[Criterion]:
        resources: list[str] = []
        for context in self._source_contexts(task_prompt):
            if not self._SOURCE_ACTION.search(context):
                continue
            urls = [
                url
                for match in self._HTTP_URL.finditer(context)
                if (url := _canonical_public_url(match.group(0).rstrip(".,;:)]}"), allow_sensitive_query=False))
            ]
            if not urls:
                continue
            endpoints = [
                endpoint
                for match in self._ABSOLUTE_ENDPOINT.finditer(context)
                if (endpoint := _safe_absolute_endpoint(match.group("endpoint")))
            ]
            if endpoints:
                for url in urls:
                    parsed = urlparse(url)
                    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
                    for endpoint in endpoints:
                        combined = _canonical_public_url(f"{origin}{endpoint}", allow_sensitive_query=False)
                        if combined is not None and combined not in resources:
                            resources.append(combined)
                continue
            for url in urls:
                if url not in resources:
                    resources.append(url)
        return [
            Criterion(
                id=_unique_id("observation", f"source-access-{resource}", used_ids),
                description=f"Public source was accessed: {resource}",
                kind=CriterionKind.OBSERVATION_EQUALS,
                source=CriterionSource.TASK_PROMPT,
                parameters={
                    "subject": _source_access_subject(resource),
                    "expected": True,
                    "resource": resource,
                },
            )
            for resource in resources
        ]

    def _source_contexts(self, task_prompt: str) -> tuple[str, ...]:
        contexts: list[str] = []
        start = 0
        for match in re.finditer(r"[\n。！？]", task_prompt):
            segment = task_prompt[start:match.start()].strip()
            if segment:
                contexts.append(segment)
            start = match.end()
        tail = task_prompt[start:].strip()
        if tail:
            contexts.append(tail)
        return tuple(contexts)

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


_SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "auth",
    "authorization",
    "client_secret",
    "code",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
}


def _source_access_subject(resource: str) -> str:
    digest = hashlib.sha256(resource.encode()).hexdigest()[:12]
    return f"source.access:{digest}"


def _canonical_public_url(raw: str, *, allow_sensitive_query: bool) -> str | None:
    cleaned = raw.strip().strip("'\"` ,;:()[]{}")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    sensitive = {
        key
        for key, _value in query_items
        if key.lower().replace("-", "_") in _SENSITIVE_QUERY_KEYS
    }
    if sensitive and not allow_sensitive_query:
        return None
    query = urlencode(
        [(key, value) for key, value in query_items if key not in sensitive],
        doseq=True,
    )
    path = parsed.path or ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def _safe_absolute_endpoint(raw: str) -> str | None:
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc:
        return None
    if any(
        key.lower().replace("-", "_") in _SENSITIVE_QUERY_KEYS
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return None
    return urlunparse(("", "", parsed.path, "", parsed.query, ""))


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


def _json_shape_from_example(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        properties = {str(key): _json_shape_from_example(item) for key, item in value.items()}
        return {
            "type": "object",
            "required": list(properties),
            "properties": properties,
        }
    if isinstance(value, list):
        item_shape = _merge_json_shapes([_json_shape_from_example(item) for item in value])
        payload: dict[str, Any] = {"type": "array"}
        if item_shape is not None:
            payload["items"] = item_shape
        return payload
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if value is None:
        return {"type": "null"}
    return {"type": "unknown"}


def _top_level_non_empty_collection_paths(example: Mapping[str, Any]) -> list[str]:
    """Return only public collection paths, never example values or evaluator facts."""

    return [f"$.{key}" for key, value in example.items() if isinstance(value, list) and value]


def _merge_json_shapes(shapes: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not shapes:
        return None
    first = dict(shapes[0])
    if len(shapes) == 1:
        return first
    if not all(shape.get("type") == first.get("type") for shape in shapes):
        return {"type": "any"}
    if first.get("type") == "object":
        property_keys = sorted(
            {key for shape in shapes for key in (shape.get("properties") or {})}
        )
        properties: dict[str, Any] = {}
        for key in property_keys:
            nested = [
                shape["properties"][key]
                for shape in shapes
                if isinstance(shape.get("properties"), Mapping) and key in shape["properties"]
            ]
            merged = _merge_json_shapes(nested)
            if merged is not None:
                properties[key] = merged
        return {
            "type": "object",
            "required": sorted(
                set.intersection(
                    *[
                        set(shape.get("required") or ())
                        for shape in shapes
                        if isinstance(shape.get("required"), Sequence)
                    ]
                )
                if shapes
                else set()
            ),
            "properties": properties,
        }
    if first.get("type") == "array":
        item_shapes = [shape["items"] for shape in shapes if isinstance(shape.get("items"), Mapping)]
        payload: dict[str, Any] = {"type": "array"}
        merged = _merge_json_shapes(item_shapes)
        if merged is not None:
            payload["items"] = merged
        return payload
    return first


def _json_list_identity_keys(shape: Mapping[str, Any]) -> dict[str, list[str]]:
    identity: dict[str, list[str]] = {}

    def visit(node: Mapping[str, Any], path: str) -> None:
        if node.get("type") == "array":
            item_shape = node.get("items")
            if isinstance(item_shape, Mapping):
                properties = item_shape.get("properties")
                if isinstance(properties, Mapping):
                    key = _preferred_identity_key(properties)
                    if key is not None:
                        identity[path] = key
                visit(item_shape, f"{path}[]")
            return
        if node.get("type") == "object":
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                for key, child in properties.items():
                    if isinstance(child, Mapping):
                        visit(child, f"{path}.{key}")

    visit(shape, "$")
    return identity


def _preferred_identity_key(properties: Mapping[str, Any]) -> list[str] | None:
    keys = [str(key) for key in properties]
    if "id" in keys:
        return ["id"]
    scoped_ids = [key for key in keys if key.endswith("_id")]
    if scoped_ids:
        return scoped_ids
    if "slug" in keys:
        for scoped in ("brand", "name", "key"):
            if scoped in keys:
                return ["slug", scoped]
        return ["slug"]
    for preferred in ("name", "key"):
        if preferred in keys:
            return [preferred]
    return None
