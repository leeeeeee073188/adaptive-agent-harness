"""Safe offline conversion from SessionLedger to DevelopmentRollout."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any

from adaptive_harness.experience_evolution import DevelopmentRollout, RolloutPartition, TrajectoryLeakageError
from adaptive_harness.ledger import SessionEvent, SessionLedger

_OUTPUT_EVENT_TYPES = {
    "assistant/message",
    "completion/checked",
    "failure/classified",
    "progress/checked",
    "recovery/decided",
    "recovery/executed",
    "recovery/outcome-evaluated",
    "resource/no-progress-checked",
    "runtime/error",
    "tool/call",
    "tool/result",
}
_STRUCTURAL_EVENT_TYPES = {
    "context/selected",
    "evidence/added",
    "request/header",
    "runtime/end",
    "runtime/start",
    "runtime/turn-end",
    "state/updated",
    "step/end",
    "step/start",
    "task/contract-created",
    "turn/end",
    "turn/start",
    "user/message",
}
_EVALUATION_EVENT_TEXT = re.compile(r"(?:judge|verifier|rubric|evaluation|expected|ground[-_/ ]?truth)", re.I)
_FORBIDDEN_KEY_PARTS = {
    "answer_key",
    "evaluation",
    "expected_answer",
    "expected_output",
    "ground_truth",
    "judge",
    "judge_reasoning",
    "private_verifier",
    "rubric",
    "selector",
    "css_selector",
    "xpath",
    "task_id",
    "verifier",
}
_FORBIDDEN_TEXT = re.compile(
    r"\b(?:expected[\s_-]+(?:answer|output)|ground[\s_-]+truth|"
    r"answer[\s_-]+key|gold[\s_-]+answer|rubric|verifier|private[\s_-]+verifier|judge)\b",
    re.IGNORECASE,
)
_TASK_SPECIFIC_TEXT = re.compile(
    r"(?:\b(?:cli|browser|file|api)-[a-z0-9-]+\b|"
    r"#[a-zA-Z][\w-]*|\[data-[\w-]+\s*=|//[a-zA-Z])",
    re.IGNORECASE,
)
_REDACTED_KEYS = {"usage", "token_usage", "prompt_tokens", "completion_tokens", "total_tokens"}
_FAILURE_UNKNOWN = "unknown_failure"
_STATE_UNKNOWN = "unknown_state"
_SURFACE_UNKNOWN = "unknown_surface"


@dataclass(frozen=True)
class PublicOutcome:
    """Caller-supplied public result summary; never inferred from evaluator-private data."""

    score: float
    passed: bool


def ledger_to_development_rollout(
    ledger: SessionLedger,
    *,
    trusted_development_task_ids: AbstractSet[str] | Sequence[str],
    outcome: PublicOutcome | Mapping[str, Any],
    blocked_task_ids: AbstractSet[str] | Sequence[str] = (),
) -> DevelopmentRollout:
    """Convert a canonical ledger into a sanitized DevelopmentRollout.

    The converter accepts only a trusted Development id allowlist and an explicit
    public outcome summary. Transfer/Held-out identities, evaluation events, and
    verifier-like fields fail closed before any rollout reaches the distiller.
    """

    development_ids = frozenset(str(item) for item in trusted_development_task_ids)
    if not development_ids:
        raise ValueError("trusted Development task set must be non-empty")
    blocked_ids = frozenset(str(item) for item in blocked_task_ids)
    public_outcome = _parse_public_outcome(outcome)

    state = ledger.project_state()
    task_id = _infer_task_id(ledger, state)
    if task_id not in development_ids:
        raise ValueError(f"task {task_id!r} is not in the trusted Development partition")
    if task_id in blocked_ids:
        raise ValueError(f"task {task_id!r} is explicitly blocked from Development rollout conversion")
    partition = _infer_partition(state)
    if partition is not RolloutPartition.DEVELOPMENT:
        raise ValueError("ledger task is not in the Development partition")

    task_state = _infer_task_state(state)
    runtime_surface = _infer_runtime_surface(state, ledger.events)
    failure_type = _infer_failure_type(ledger.events)

    replacement_ids = tuple(sorted(development_ids | {task_id}))
    blocked_patterns = tuple(item for item in sorted(blocked_ids - {task_id}) if item)
    sanitized_events = tuple(
        _canonicalize_event(event, replacement_ids, blocked_patterns)
        for event in ledger.events
        if _admit_event_type(event.type)
    )
    if not sanitized_events:
        raise ValueError("ledger contains no admitted runtime/policy events")

    fingerprint = _fingerprint(
        {
            "task_id": task_id,
            "task_state": task_state,
            "failure_type": failure_type,
            "runtime_surface": runtime_surface,
            "outcome_score": public_outcome.score,
            "passed": public_outcome.passed,
            "events": sanitized_events,
        }
    )
    rollout = DevelopmentRollout(
        task_id=task_id,
        partition=RolloutPartition.DEVELOPMENT,
        trajectory_ref=f"ledger-rollout-sha256:{fingerprint}",
        task_state=task_state,
        failure_type=failure_type,
        runtime_surface=runtime_surface,
        outcome_score=public_outcome.score,
        passed=public_outcome.passed,
        events=sanitized_events,
    )
    rollout.validate()
    return rollout


def _parse_public_outcome(outcome: PublicOutcome | Mapping[str, Any]) -> PublicOutcome:
    if isinstance(outcome, PublicOutcome):
        score = outcome.score
        passed = outcome.passed
    else:
        if not {"score", "passed"}.issubset(outcome):
            raise ValueError("explicit public outcome requires score and passed")
        score = outcome["score"]
        passed = outcome["passed"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("public outcome score must be numeric")
    numeric_score = float(score)
    if not 0.0 <= numeric_score <= 1.0:
        raise ValueError("public outcome score must be between 0 and 1")
    if not isinstance(passed, bool):
        raise ValueError("public outcome passed must be boolean")
    return PublicOutcome(numeric_score, passed)


def _infer_task_id(ledger: SessionLedger, state: Mapping[str, Any]) -> str:
    candidates: list[Any] = [state.get("task_id")]
    task = state.get("task")
    if isinstance(task, Mapping):
        candidates.append(task.get("task_id") or task.get("id"))
    for event in ledger.events:
        if event.type == "task/contract-created":
            contract = event.payload.get("contract")
            if isinstance(contract, Mapping):
                candidates.append(contract.get("task_id"))
    for candidate in candidates:
        task_id = str(candidate or "").strip()
        if task_id:
            return task_id
    raise ValueError("ledger does not expose a public task_id")


def _infer_partition(state: Mapping[str, Any]) -> RolloutPartition:
    raw = str(state.get("partition") or state.get("task_partition") or RolloutPartition.DEVELOPMENT.value)
    normalized = raw.casefold().replace("-", "").replace("_", "")
    if normalized in {"development", "dev"}:
        return RolloutPartition.DEVELOPMENT
    if normalized in {"transfer"}:
        return RolloutPartition.TRANSFER
    if normalized in {"heldout", "held"}:
        return RolloutPartition.HELDOUT
    raise ValueError(f"unknown rollout partition: {raw!r}")


def _infer_task_state(state: Mapping[str, Any]) -> str:
    for key in ("task_state", "phase", "state"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    task = state.get("task")
    if isinstance(task, Mapping):
        for key in ("task_state", "phase", "state"):
            value = task.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _STATE_UNKNOWN


def _infer_runtime_surface(state: Mapping[str, Any], events: Sequence[SessionEvent]) -> str:
    for key in ("runtime_surface", "surface", "runtime"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for event in events:
        if event.type == "tool/call":
            name = str(event.payload.get("name") or "").strip()
            if name:
                return name
    return _SURFACE_UNKNOWN


def _infer_failure_type(events: Sequence[SessionEvent]) -> str:
    for event in reversed(events):
        if event.type == "failure/classified":
            failure = event.payload.get("failure")
            if isinstance(failure, Mapping):
                error_type = str(failure.get("error_type") or "").strip()
                if error_type:
                    return error_type
        if event.type == "tool/result":
            error_type = str(event.payload.get("error_type") or "").strip()
            if error_type and error_type.lower() != "none":
                return error_type
        if event.type == "runtime/error":
            error_type = str(event.payload.get("type") or "UNKNOWN").strip()
            return f"RUNTIME_ERROR:{error_type}"
    return _FAILURE_UNKNOWN


def _admit_event_type(event_type: str) -> bool:
    if event_type in _OUTPUT_EVENT_TYPES:
        return True
    if event_type in _STRUCTURAL_EVENT_TYPES:
        return False
    if _EVALUATION_EVENT_TEXT.search(event_type):
        raise TrajectoryLeakageError(f"ledger contains evaluation-only event: {event_type!r}")
    raise TrajectoryLeakageError(f"ledger contains non-admitted event type: {event_type!r}")


def _canonicalize_event(
    event: SessionEvent,
    replacement_task_ids: tuple[str, ...],
    blocked_task_ids: tuple[str, ...],
) -> Mapping[str, Any]:
    payload = _sanitize_value(event.payload, replacement_task_ids, blocked_task_ids)
    result: dict[str, Any] = {"type": event.type, "payload": payload}
    if event.turn is not None:
        result["turn"] = event.turn
    if event.step is not None:
        result["step"] = event.step
    return result


def _sanitize_value(
    value: Any,
    replacement_task_ids: tuple[str, ...],
    blocked_task_ids: tuple[str, ...],
) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, nested in sorted(value.items(), key=lambda item: str(item[0])):
            key_text = str(key)
            normalized = _normalize_key(key_text)
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise TrajectoryLeakageError(f"trajectory contains evaluation-only field: {key_text!r}")
            if normalized in _REDACTED_KEYS:
                continue
            sanitized[key_text] = _sanitize_value(nested, replacement_task_ids, blocked_task_ids)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, replacement_task_ids, blocked_task_ids) for item in value]
    if isinstance(value, str):
        if _FORBIDDEN_TEXT.search(value):
            raise TrajectoryLeakageError("trajectory contains evaluation-only text")
        sanitized_text = value
        for task_id in blocked_task_ids:
            if re.search(re.escape(task_id), sanitized_text, flags=re.IGNORECASE):
                raise TrajectoryLeakageError("trajectory contains blocked task identity")
        for task_id in replacement_task_ids:
            sanitized_text = re.sub(re.escape(task_id), "[TASK_ID]", sanitized_text, flags=re.IGNORECASE)
        if _TASK_SPECIFIC_TEXT.search(sanitized_text):
            raise TrajectoryLeakageError("trajectory contains task-specific identity or selector text")
        return sanitized_text
    return value


def _normalize_key(key: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
    return normalized.casefold().replace("-", "_").replace(" ", "_")


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
