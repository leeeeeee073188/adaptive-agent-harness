"""Semantic progress detection over replayable task facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from adaptive_harness.task_state import TaskState


class ProgressStatus(StrEnum):
    PROGRESSED = "progressed"
    NO_PROGRESS = "no_progress"
    REGRESSED = "regressed"


@dataclass(frozen=True)
class ProgressSnapshot:
    evidence: dict[str, Any]
    task_values: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True)
class ProgressResult:
    status: ProgressStatus
    added_evidence: tuple[str, ...]
    changed_evidence: tuple[str, ...]
    changed_state: tuple[str, ...]
    before_fingerprint: str
    after_fingerprint: str
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "added_evidence": list(self.added_evidence),
            "changed_evidence": list(self.changed_evidence),
            "changed_state": list(self.changed_state),
            "before_fingerprint": self.before_fingerprint,
            "after_fingerprint": self.after_fingerprint,
            "reason": self.reason,
        }


class ProgressDetector(Protocol):
    def snapshot(self, state: TaskState) -> ProgressSnapshot: ...

    def detect(self, before: ProgressSnapshot, after: ProgressSnapshot) -> ProgressResult: ...


class RuleBasedProgressDetector:
    """Count novel task facts, not tool activity or Harness control flags."""

    _CONTROL_PREFIXES = ("recovery.", "progress.")

    def snapshot(self, state: TaskState) -> ProgressSnapshot:
        evidence: dict[str, Any] = {}
        for item in state.evidence:
            evidence[f"{item.kind.value}:{item.subject}"] = item.value
        task_values = {
            str(key): value
            for key, value in state.values.items()
            if not str(key).startswith(self._CONTROL_PREFIXES)
        }
        payload = {"evidence": evidence, "task_values": task_values}
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ProgressSnapshot(evidence, task_values, hashlib.sha256(canonical.encode()).hexdigest())

    def detect(self, before: ProgressSnapshot, after: ProgressSnapshot) -> ProgressResult:
        added = tuple(sorted(set(after.evidence) - set(before.evidence)))
        changed = tuple(
            sorted(
                key
                for key in set(before.evidence) & set(after.evidence)
                if before.evidence[key] != after.evidence[key]
            )
        )
        changed_state = tuple(
            sorted(
                key
                for key in set(before.task_values) | set(after.task_values)
                if before.task_values.get(key) != after.task_values.get(key)
            )
        )
        regressed = any(
            _truth(before.evidence[key]) and not _truth(after.evidence[key])
            for key in changed
        )
        if regressed:
            status = ProgressStatus.REGRESSED
            reason = "Previously positive task evidence regressed."
        elif added or changed or changed_state:
            status = ProgressStatus.PROGRESSED
            reason = "Novel task evidence or non-control state was recorded."
        else:
            status = ProgressStatus.NO_PROGRESS
            reason = "No semantic task fact changed; tool activity alone is ignored."
        return ProgressResult(
            status,
            added,
            changed,
            changed_state,
            before.fingerprint,
            after.fingerprint,
            reason,
        )


def _truth(value: Any) -> bool:
    if isinstance(value, dict) and "exists" in value:
        return value.get("exists") is True
    return value is True
