"""Replayable tool intent/resource facts and bounded verification decisions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from adaptive_harness.capabilities import ToolCall, ToolResult


class ToolIntent(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    SEARCH = "search"
    TRANSFORM = "transform"
    WRITE = "write"
    VERIFY = "verify"
    PRESENT = "present"
    UNKNOWN = "unknown"


class VerificationDisposition(StrEnum):
    ALLOW = "allow"
    WARN = "warn"


class VerificationMode(StrEnum):
    OBSERVE = "observe"
    ADVISE = "advise"


@dataclass(frozen=True)
class ActionSemantics:
    intent: ToolIntent
    resources: tuple[str, ...]
    fields: tuple[str, ...]
    argument_fingerprint: str
    argument_keys: tuple[str, ...]
    redacted_argument_keys: tuple[str, ...]

    @property
    def mutating(self) -> bool:
        return self.intent in {ToolIntent.TRANSFORM, ToolIntent.WRITE}

    @property
    def scope_key(self) -> str:
        scope = self.fields or self.resources or (self.argument_fingerprint,)
        encoded = json.dumps(
            {"intent": self.intent.value, "scope": scope},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()[:20]


@dataclass(frozen=True)
class ActionRecord:
    sequence: int
    call_id: str
    tool_name: str
    semantics: ActionSemantics
    result_sha256: str
    result_chars: int
    result_preview: str
    error_type: str | None
    mutation_epoch: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "intent": self.semantics.intent.value,
            "resources": list(self.semantics.resources),
            "fields": list(self.semantics.fields),
            "argument_fingerprint": self.semantics.argument_fingerprint,
            "scope_key": self.semantics.scope_key,
            "result_sha256": self.result_sha256,
            "result_chars": self.result_chars,
            "result_preview": self.result_preview,
            "error_type": self.error_type,
            "mutation_epoch": self.mutation_epoch,
        }


@dataclass(frozen=True)
class ActionCluster:
    scope_key: str
    intent: ToolIntent
    resources: tuple[str, ...]
    fields: tuple[str, ...]
    attempts: int
    distinct_results: int
    mutation_epoch: int
    record_sequences: tuple[int, ...]

    @property
    def repeated_unchanged(self) -> bool:
        return self.attempts > 1 and self.distinct_results == 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "scope_key": self.scope_key,
            "intent": self.intent.value,
            "resources": list(self.resources),
            "fields": list(self.fields),
            "attempts": self.attempts,
            "distinct_results": self.distinct_results,
            "mutation_epoch": self.mutation_epoch,
            "record_sequences": list(self.record_sequences),
            "repeated_unchanged": self.repeated_unchanged,
        }


@dataclass(frozen=True)
class VerificationBudgetConfig:
    max_same_scope: int = 2
    max_total_since_mutation: int = 4
    mode: VerificationMode = VerificationMode.OBSERVE

    def __post_init__(self) -> None:
        if self.max_same_scope < 1 or self.max_total_since_mutation < 1:
            raise ValueError("verification budgets must be positive")


@dataclass(frozen=True)
class VerificationDecision:
    disposition: VerificationDisposition
    scope_attempts: int
    total_since_mutation: int
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "scope_attempts": self.scope_attempts,
            "total_since_mutation": self.total_since_mutation,
            "reason": self.reason,
        }


class ToolActionLedger:
    """Run-local command side; the records themselves are replayable facts."""

    def __init__(self, config: VerificationBudgetConfig | None = None) -> None:
        self.config = config or VerificationBudgetConfig()
        self._records: list[ActionRecord] = []
        self._decisions: list[VerificationDecision] = []
        self._mutation_epoch = 0
        self._verification_total = 0
        self._verification_scopes: dict[str, int] = {}

    @property
    def records(self) -> tuple[ActionRecord, ...]:
        return tuple(self._records)

    @property
    def decisions(self) -> tuple[VerificationDecision, ...]:
        return tuple(self._decisions)

    def observe(self, call: ToolCall, result: ToolResult) -> tuple[ActionRecord, VerificationDecision]:
        semantics = classify_tool_action(call.name, call.arguments)
        if semantics.mutating and result.error_type is None:
            self._mutation_epoch += 1
            self._verification_total = 0
            self._verification_scopes.clear()
        rendered = result.content
        record = ActionRecord(
            sequence=len(self._records) + 1,
            call_id=call.id,
            tool_name=call.name,
            semantics=semantics,
            result_sha256=hashlib.sha256(rendered.encode()).hexdigest(),
            result_chars=len(rendered),
            result_preview=_safe_preview(rendered),
            error_type=result.error_type,
            mutation_epoch=self._mutation_epoch,
        )
        self._records.append(record)
        if semantics.intent is not ToolIntent.VERIFY:
            decision = VerificationDecision(
                VerificationDisposition.ALLOW,
                0,
                self._verification_total,
                "Action is not a verification call.",
            )
            self._decisions.append(decision)
            return record, decision
        self._verification_total += 1
        scope_attempts = self._verification_scopes.get(semantics.scope_key, 0) + 1
        self._verification_scopes[semantics.scope_key] = scope_attempts
        exceeded = (
            scope_attempts > self.config.max_same_scope
            or self._verification_total > self.config.max_total_since_mutation
        )
        decision = VerificationDecision(
            VerificationDisposition.WARN if exceeded else VerificationDisposition.ALLOW,
            scope_attempts,
            self._verification_total,
            (
                "Verification budget exceeded without an intervening successful mutation; "
                "deliver or change strategy instead of adding another equivalent check."
                if exceeded
                else "Verification remains within the post-mutation budget."
            ),
        )
        self._decisions.append(decision)
        return record, decision

    def clusters(self) -> tuple[ActionCluster, ...]:
        grouped: dict[tuple[int, str], list[ActionRecord]] = {}
        for record in self._records:
            grouped.setdefault((record.mutation_epoch, record.semantics.scope_key), []).append(record)
        clusters = []
        for (epoch, scope_key), records in grouped.items():
            first = records[0]
            clusters.append(
                ActionCluster(
                    scope_key,
                    first.semantics.intent,
                    first.semantics.resources,
                    first.semantics.fields,
                    len(records),
                    len({record.result_sha256 for record in records}),
                    epoch,
                    tuple(record.sequence for record in records),
                )
            )
        return tuple(sorted(clusters, key=lambda item: item.record_sequences[-1]))

    @classmethod
    def from_messages(
        cls,
        messages: Sequence[Mapping[str, Any]],
        config: VerificationBudgetConfig | None = None,
    ) -> ToolActionLedger:
        ledger = cls(config)
        pending: dict[str, ToolCall] = {}
        for message in messages:
            if message.get("role") == "assistant":
                for raw in message.get("tool_calls") or ():
                    if not isinstance(raw, Mapping) or raw.get("id") is None:
                        continue
                    call_id = str(raw["id"])
                    pending[call_id] = ToolCall(
                        call_id,
                        str(raw.get("name") or "unknown"),
                        dict(raw.get("arguments") or raw.get("args") or {}),
                    )
            elif message.get("role") == "tool":
                call_id = str(message.get("tool_call_id") or "")
                call = pending.pop(call_id, None)
                if call is None:
                    continue
                content = message.get("content", "")
                rendered = content if isinstance(content, str) else json.dumps(content, default=str)
                ledger.observe(
                    call,
                    ToolResult(call_id, rendered, error_type=message.get("error_type")),
                )
        return ledger


def classify_tool_action(tool_name: str, arguments: Mapping[str, Any]) -> ActionSemantics:
    name = tool_name.strip().lower()
    description = str(arguments.get("description") or arguments.get("reason") or "").lower()
    command = str(arguments.get("command") or arguments.get("cmd") or "")
    if name in {"bash", "shell", "exec", "run_command"}:
        intent = _bash_intent(command, description)
    elif any(token in name for token in ("present", "deliver", "submit")):
        intent = ToolIntent.PRESENT
    elif any(token in name for token in ("write", "edit", "replace", "patch", "delete", "remove", "create")):
        intent = ToolIntent.WRITE
    elif any(token in name for token in ("verify", "validate", "check", "test")):
        intent = ToolIntent.VERIFY
    elif name in {"ls", "list", "glob"} or name.startswith("list_"):
        intent = ToolIntent.DISCOVER
    elif any(token in name for token in ("grep", "search", "find")):
        intent = ToolIntent.SEARCH
    elif any(token in name for token in ("read", "fetch", "get_text", "snapshot")):
        intent = ToolIntent.READ
    else:
        intent = ToolIntent.UNKNOWN
    semantic_arguments = _semantic_arguments(arguments)
    encoded = json.dumps(semantic_arguments, ensure_ascii=False, sort_keys=True, default=str).encode()
    return ActionSemantics(
        intent,
        _extract_resources(arguments, command),
        _extract_fields(command),
        hashlib.sha256(encoded).hexdigest()[:20],
        tuple(sorted(str(key) for key in arguments if _normalized_key(key) not in _NON_SEMANTIC_ARGUMENTS)),
        tuple(sorted(str(key) for key in arguments if _normalized_key(key) in _SENSITIVE_ARGUMENTS)),
    )


_VERIFY_HINT = re.compile(r"\b(?:verify|validate|check|confirm|audit|assert|inspect)\b", re.I)
_MUTATION = re.compile(
    r"(?:\b(?:rm|mv|cp|mkdir|touch|tee)\b|sed\s+-i\b|(?:write_text|write_bytes|to_csv|json\.dump(?!s)|csv\.writer)\s*\(|open\s*\([^\n]{0,160}['\"](?:w|a|x)[+b]?['\"]|>{1,2}\s*(?!/dev/null)(?:/task/|(?:outputs|workspace|tmp)/|[A-Za-z][A-Za-z0-9_-]*\.(?:csv|json|md|txt|yaml|html)))",
    re.I,
)
_VERIFY_COMMAND = re.compile(
    r"(?:\bassert\b|\bpytest\b|\bunittest\b|\bwc\s+-l\b|\bsha256sum\b|\bhead\s+-|\btail\s+-|sorted\s*:|validate|verify)",
    re.I,
)
_DISCOVER_COMMAND = re.compile(r"^\s*(?:ls|find|tree)\b", re.I)
_SEARCH_COMMAND = re.compile(r"\b(?:grep|rg)\b", re.I)


def _bash_intent(command: str, description: str) -> ToolIntent:
    if _MUTATION.search(command):
        return ToolIntent.WRITE
    if _VERIFY_HINT.search(description) or _VERIFY_COMMAND.search(command):
        return ToolIntent.VERIFY
    if _SEARCH_COMMAND.search(command):
        return ToolIntent.SEARCH
    if _DISCOVER_COMMAND.search(command):
        return ToolIntent.DISCOVER
    if command.strip():
        return ToolIntent.READ
    return ToolIntent.UNKNOWN


_RESOURCE = re.compile(
    r"(?:/task/)?(?:workspace|outputs|snapshots|tmp)(?:/[A-Za-z0-9_.*?{}\-]+)+(?:\.[A-Za-z0-9]{1,10})?"
)
_ABSOLUTE_TASK_RESOURCE = re.compile(r"/task/[A-Za-z0-9_.*?{}\-/]+(?:\.[A-Za-z0-9]{1,10})?")
_FILE = re.compile(r"\b[A-Za-z0-9_-]+\.(?:json|csv|md|txt|yaml|yml|toml|html|png|jpg|pdf)\b", re.I)
_FIELD_PATTERNS = (
    re.compile(r"\[['\"]([A-Za-z_][A-Za-z0-9_-]{1,48})['\"]\]"),
    re.compile(r"\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_-]{1,48})['\"]"),
    re.compile(r"['\"]([a-z][a-z0-9_]{2,48})['\"]"),
)


def _extract_resources(arguments: Mapping[str, Any], command: str) -> tuple[str, ...]:
    values: list[str] = []
    for key, value in arguments.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in {"path", "file", "directory", "url", "endpoint"} and isinstance(value, str):
            values.append(value)
    values.extend(_RESOURCE.findall(command))
    values.extend(_ABSOLUTE_TASK_RESOURCE.findall(command))
    values.extend(_FILE.findall(command))
    return tuple(sorted({_normalize_resource(value) for value in values if value.strip()}))


def _extract_fields(command: str) -> tuple[str, ...]:
    fields = {
        match
        for pattern in _FIELD_PATTERNS
        for match in pattern.findall(command)
        if "/" not in match and "." not in match and match not in _FIELD_STOPWORDS
    }
    return tuple(sorted(fields))


_FIELD_STOPWORDS = {
    "true",
    "false",
    "none",
    "utf_8",
    "python",
    "outputs",
    "workspace",
    "snapshots",
}


def _normalize_resource(value: str) -> str:
    normalized = value.strip("'\"` ,;:()[]{}").replace("\\", "/")
    return normalized.removeprefix("/task/")


_NON_SEMANTIC_ARGUMENTS = {"description", "reason", "label"}
_SENSITIVE_ARGUMENTS = {"api_key", "apikey", "authorization", "password", "secret", "token"}


def _semantic_arguments(value: Mapping[str, Any]) -> Mapping[str, Any]:
    result = {}
    for key, item in value.items():
        normalized = _normalized_key(key)
        if normalized in _NON_SEMANTIC_ARGUMENTS:
            continue
        result[str(key)] = "<redacted>" if normalized in _SENSITIVE_ARGUMENTS else item
    return result


def _normalized_key(value: Any) -> str:
    return str(value).lower().replace("-", "_")


_SECRET_VALUE = re.compile(r"(?i)(?:bearer\s+)?sk-[a-z0-9_-]{12,}")


def _safe_preview(text: str, *, head: int = 180, tail: int = 80) -> str:
    scrubbed = _SECRET_VALUE.sub("<redacted>", text)
    if len(scrubbed) <= head + tail:
        return scrubbed
    return f"{scrubbed[:head]}…<{len(scrubbed) - head - tail} chars omitted>…{scrubbed[-tail:]}"
