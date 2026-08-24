"""Append-only Offline Experience lifecycle and deterministic retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from adaptive_harness.ledger import SessionEvent, SessionLedger

EXPERIENCE_CANDIDATE_CREATED = "experience/candidate-created"
EXPERIENCE_SHADOW_STARTED = "experience/shadow-started"
EXPERIENCE_PROMOTED = "experience/promoted"
EXPERIENCE_QUARANTINED = "experience/quarantined"
EXPERIENCE_RETIRED = "experience/retired"
EXPERIENCE_RETRIEVAL_OUTCOME_RECORDED = "experience/retrieval-outcome-recorded"


class ExperienceStatus(StrEnum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class ExperienceLifecycle(StrEnum):
    CANDIDATE_CREATED = EXPERIENCE_CANDIDATE_CREATED
    SHADOW_STARTED = EXPERIENCE_SHADOW_STARTED
    PROMOTED = EXPERIENCE_PROMOTED
    QUARANTINED = EXPERIENCE_QUARANTINED
    RETIRED = EXPERIENCE_RETIRED


@dataclass(frozen=True)
class Experience:
    """A transferable strategy admitted by trigger state, not by task identity."""

    experience_id: str
    version: int
    task_state: str
    failure_type: str
    runtime_surface: str
    situation: str
    strategy: str
    anti_pattern: str
    progress_signal: str
    stop_condition: str
    source_task_ids: tuple[str, ...]

    def validate_candidate(self) -> None:
        self._validate_shape()
        if len(set(self.source_task_ids)) < 3:
            raise ValueError("candidate Experience requires >=3 distinct Development source tasks")
        report = ExperienceAdmissibility().check(self)
        if not report.passed:
            raise ValueError(f"candidate Experience failed admissibility: {', '.join(report.reasons)}")

    def _validate_shape(self) -> None:
        text_fields = (
            self.experience_id,
            self.task_state,
            self.failure_type,
            self.runtime_surface,
            self.situation,
            self.strategy,
            self.anti_pattern,
            self.progress_signal,
            self.stop_condition,
        )
        if any(not field.strip() for field in text_fields):
            raise ValueError("Experience fields must be non-empty")
        if self.version < 1:
            raise ValueError("Experience version must be positive")
        if any(not source.strip() for source in self.source_task_ids):
            raise ValueError("Experience source task IDs must be non-empty")

    @property
    def key(self) -> tuple[str, int]:
        return (self.experience_id, self.version)

    def to_payload(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "version": self.version,
            "task_state": self.task_state,
            "failure_type": self.failure_type,
            "runtime_surface": self.runtime_surface,
            "situation": self.situation,
            "strategy": self.strategy,
            "anti_pattern": self.anti_pattern,
            "progress_signal": self.progress_signal,
            "stop_condition": self.stop_condition,
            "source_task_ids": list(self.source_task_ids),
        }

    @property
    def content_fingerprint(self) -> str:
        """Identity of transferable content, excluding version and provenance."""

        values = (
            self.task_state,
            self.failure_type,
            self.runtime_surface,
            self.situation,
            self.strategy,
            self.anti_pattern,
            self.progress_signal,
            self.stop_condition,
        )
        canonical = json.dumps(
            [" ".join(value.casefold().split()) for value in values],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Experience:
        return cls(
            experience_id=str(payload["experience_id"]),
            version=int(payload["version"]),
            task_state=str(payload["task_state"]),
            failure_type=str(payload["failure_type"]),
            runtime_surface=str(payload["runtime_surface"]),
            situation=str(payload["situation"]),
            strategy=str(payload["strategy"]),
            anti_pattern=str(payload["anti_pattern"]),
            progress_signal=str(payload["progress_signal"]),
            stop_condition=str(payload["stop_condition"]),
            source_task_ids=tuple(str(item) for item in payload["source_task_ids"]),
        )


@dataclass(frozen=True)
class AdmissibilityReport:
    passed: bool
    reasons: tuple[str, ...]


class ExperienceAdmissibility:
    """Fail-closed leakage screen for candidate Experience content."""

    _BANNED_MARKERS = (
        "ground truth",
        "expected answer",
        "expected output",
        "verifier-private",
        "verifier private",
        "private verifier",
        "answer key",
        "gold answer",
        "css selector",
        "xpath",
        "selector",
    )
    _BANNED_PATTERNS = (
        re.compile(r"\b(?:cli|browser|file|api)-[a-z0-9-]+\b", re.IGNORECASE),
        re.compile(r"(?:#[a-zA-Z][\w-]*|\[data-[\w-]+\s*=|//[a-zA-Z])"),
    )

    def check(self, experience: Experience) -> AdmissibilityReport:
        reasons: list[str] = []
        fields = {
            "situation": experience.situation,
            "strategy": experience.strategy,
            "anti_pattern": experience.anti_pattern,
            "progress_signal": experience.progress_signal,
            "stop_condition": experience.stop_condition,
        }
        for field_name, value in fields.items():
            lowered = value.casefold()
            for marker in self._BANNED_MARKERS:
                if marker in lowered:
                    reasons.append(f"{field_name} contains banned marker {marker!r}")
            for source_task_id in set(experience.source_task_ids):
                if source_task_id.casefold() in lowered:
                    reasons.append(f"{field_name} contains source task id {source_task_id!r}")
            for pattern in self._BANNED_PATTERNS:
                if pattern.search(value):
                    reasons.append(f"{field_name} contains task-specific identity or selector")
        return AdmissibilityReport(not reasons, tuple(reasons))


@dataclass(frozen=True)
class TransferValidation:
    validation_task_ids: tuple[str, ...]
    stable_pass_regressions: int
    harm_observed: bool
    evidence_ref: str
    effective_task_ids: tuple[str, ...] = ()
    no_progress_regressions: int = 0

    def validate_for(self, experience: Experience) -> None:
        reasons: list[str] = []
        if len(set(self.validation_task_ids)) < 2:
            reasons.append("requires >=2 distinct transfer validation tasks")
        overlapping = set(self.validation_task_ids).intersection(experience.source_task_ids)
        if overlapping:
            reasons.append("transfer validation tasks must be disjoint from Development sources")
        if self.stable_pass_regressions != 0:
            reasons.append("stable-pass regressions must be zero")
        if self.harm_observed:
            reasons.append("harm must not be observed")
        if self.no_progress_regressions != 0:
            reasons.append("no-progress regressions must be zero")
        effective = set(self.effective_task_ids)
        if not effective:
            reasons.append("requires effectiveness on at least one transfer task")
        if not effective.issubset(set(self.validation_task_ids)):
            reasons.append("effective tasks must belong to transfer validation tasks")
        if not self.evidence_ref.strip():
            reasons.append("evidence_ref must be non-empty")
        if reasons:
            raise ValueError(f"transfer validation failed: {', '.join(reasons)}")

    def to_payload(self) -> dict[str, Any]:
        return {
            "validation_task_ids": list(self.validation_task_ids),
            "stable_pass_regressions": self.stable_pass_regressions,
            "harm_observed": self.harm_observed,
            "evidence_ref": self.evidence_ref,
            "effective_task_ids": list(self.effective_task_ids),
            "no_progress_regressions": self.no_progress_regressions,
        }


@dataclass(frozen=True)
class RetrievalOutcome:
    task_ref: str
    adopted: bool
    progress_signal_observed: bool
    task_success: bool
    harm_observed: bool
    evidence_ref: str

    def validate(self) -> None:
        if not self.task_ref.strip() or not self.evidence_ref.strip():
            raise ValueError("retrieval outcome task_ref and evidence_ref must be non-empty")
        if not self.adopted and (
            self.progress_signal_observed or self.task_success or self.harm_observed
        ):
            raise ValueError("an unadopted Experience cannot be credited with an outcome")
        if self.task_success and self.harm_observed:
            raise ValueError("retrieval outcome cannot be both successful and harmful")

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "adopted": self.adopted,
            "progress_signal_observed": self.progress_signal_observed,
            "task_success": self.task_success,
            "harm_observed": self.harm_observed,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RetrievalOutcome:
        return cls(
            task_ref=str(payload["task_ref"]),
            adopted=bool(payload["adopted"]),
            progress_signal_observed=bool(payload["progress_signal_observed"]),
            task_success=bool(payload["task_success"]),
            harm_observed=bool(payload["harm_observed"]),
            evidence_ref=str(payload["evidence_ref"]),
        )


@dataclass(frozen=True)
class RetrievalQuery:
    task_state: str
    failure_type: str
    runtime_surface: str
    limit: int = 3

    def __post_init__(self) -> None:
        if not self.task_state.strip() or not self.failure_type.strip() or not self.runtime_surface.strip():
            raise ValueError("retrieval query fields must be non-empty")
        if self.limit < 1:
            raise ValueError("retrieval limit must be positive")


@dataclass(frozen=True)
class ExperienceState:
    experiences: Mapping[tuple[str, int], Experience]
    lifecycle: Mapping[tuple[str, int], ExperienceStatus]
    history: Mapping[tuple[str, int], tuple[ExperienceLifecycle, ...]]
    retrieval_outcomes: Mapping[tuple[str, int], tuple[RetrievalOutcome, ...]]


class ExperienceProjector:
    def project(self, events: Iterable[SessionEvent]) -> ExperienceState:
        experiences: dict[tuple[str, int], Experience] = {}
        lifecycle: dict[tuple[str, int], ExperienceStatus] = {}
        history: dict[tuple[str, int], list[ExperienceLifecycle]] = {}
        retrieval_outcomes: dict[tuple[str, int], list[RetrievalOutcome]] = {}
        for event in events:
            if event.type == EXPERIENCE_RETRIEVAL_OUTCOME_RECORDED:
                key = self._key(event.payload)
                retrieval_outcomes.setdefault(key, []).append(
                    RetrievalOutcome.from_payload(event.payload["outcome"])
                )
                continue
            lifecycle_event = self._to_lifecycle(event.type)
            if lifecycle_event is None:
                continue
            key = self._key(event.payload)
            if lifecycle_event == ExperienceLifecycle.CANDIDATE_CREATED:
                experiences[key] = Experience.from_payload(event.payload["experience"])
                lifecycle[key] = ExperienceStatus.CANDIDATE
            elif lifecycle_event == ExperienceLifecycle.SHADOW_STARTED:
                lifecycle[key] = ExperienceStatus.SHADOW
            elif lifecycle_event == ExperienceLifecycle.PROMOTED:
                lifecycle[key] = ExperienceStatus.PROMOTED
            elif lifecycle_event == ExperienceLifecycle.QUARANTINED:
                lifecycle[key] = ExperienceStatus.QUARANTINED
            elif lifecycle_event == ExperienceLifecycle.RETIRED:
                lifecycle[key] = ExperienceStatus.RETIRED
            history.setdefault(key, []).append(lifecycle_event)
        return ExperienceState(
            experiences=experiences,
            lifecycle=lifecycle,
            history={key: tuple(value) for key, value in history.items()},
            retrieval_outcomes={
                key: tuple(value) for key, value in retrieval_outcomes.items()
            },
        )

    def _to_lifecycle(self, event_type: str) -> ExperienceLifecycle | None:
        try:
            return ExperienceLifecycle(event_type)
        except ValueError:
            return None

    def _key(self, payload: Mapping[str, Any]) -> tuple[str, int]:
        return (str(payload["experience_id"]), int(payload["version"]))


class ExperienceStore:
    """Governed append-only store for Offline Experience Evolution."""

    def __init__(self, ledger: SessionLedger) -> None:
        self.ledger = ledger
        self._projector = ExperienceProjector()

    @property
    def state(self) -> ExperienceState:
        return self._projector.project(self.ledger.events)

    def create_candidate(self, experience: Experience) -> None:
        experience.validate_candidate()
        state = self.state
        if experience.key in state.experiences:
            raise ValueError("Experience candidate already exists")
        if any(
            existing.content_fingerprint == experience.content_fingerprint
            for existing in state.experiences.values()
        ):
            raise ValueError("duplicate transferable Experience content")
        self.ledger.append(
            EXPERIENCE_CANDIDATE_CREATED,
            {
                "experience_id": experience.experience_id,
                "version": experience.version,
                "experience": experience.to_payload(),
            },
        )

    def start_shadow(self, experience_id: str, version: int, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("shadow reason must be non-empty")
        key = (experience_id, version)
        self._require_status(key, ExperienceStatus.CANDIDATE)
        self.ledger.append(
            EXPERIENCE_SHADOW_STARTED,
            {"experience_id": experience_id, "version": version, "reason": reason},
        )

    def promote(self, experience_id: str, version: int, validation: TransferValidation) -> None:
        key = (experience_id, version)
        state = self.state
        self._require_status(key, ExperienceStatus.SHADOW, state)
        experience = state.experiences[key]
        validation.validate_for(experience)
        self.ledger.append(
            EXPERIENCE_PROMOTED,
            {
                "experience_id": experience_id,
                "version": version,
                "transfer_validation": validation.to_payload(),
            },
        )

    def quarantine(self, experience_id: str, version: int, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("quarantine reason must be non-empty")
        key = (experience_id, version)
        self._require_status(key, ExperienceStatus.PROMOTED)
        self.ledger.append(
            EXPERIENCE_QUARANTINED,
            {"experience_id": experience_id, "version": version, "reason": reason},
        )

    def retire(self, experience_id: str, version: int, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("retirement reason must be non-empty")
        key = (experience_id, version)
        self._require_status(key, ExperienceStatus.QUARANTINED)
        self.ledger.append(
            EXPERIENCE_RETIRED,
            {"experience_id": experience_id, "version": version, "reason": reason},
        )

    def retrieve(self, query: RetrievalQuery) -> tuple[Experience, ...]:
        state = self.state
        limit = min(query.limit, 3)
        matches = [
            experience
            for key, experience in state.experiences.items()
            if state.lifecycle.get(key) == ExperienceStatus.PROMOTED
            and experience.task_state == query.task_state
            and experience.failure_type == query.failure_type
            and experience.runtime_surface == query.runtime_surface
        ]
        latest_by_id: dict[str, Experience] = {}
        for experience in matches:
            current = latest_by_id.get(experience.experience_id)
            if current is None or experience.version > current.version:
                latest_by_id[experience.experience_id] = experience
        return tuple(
            sorted(latest_by_id.values(), key=lambda item: (item.experience_id, item.version))[:limit]
        )

    def record_retrieval_outcome(
        self,
        experience_id: str,
        version: int,
        outcome: RetrievalOutcome,
    ) -> None:
        outcome.validate()
        key = (experience_id, version)
        status = self.state.lifecycle.get(key)
        if status not in {ExperienceStatus.PROMOTED, ExperienceStatus.QUARANTINED}:
            raise ValueError("retrieval outcomes require a promoted or quarantined Experience")
        self.ledger.append(
            EXPERIENCE_RETRIEVAL_OUTCOME_RECORDED,
            {
                "experience_id": experience_id,
                "version": version,
                "outcome": outcome.to_payload(),
            },
        )

    def _require_status(
        self,
        key: tuple[str, int],
        expected: ExperienceStatus,
        state: ExperienceState | None = None,
    ) -> None:
        current_state = state or self.state
        actual = current_state.lifecycle.get(key)
        if actual != expected:
            raise ValueError(f"Experience {key[0]}@{key[1]} must be {expected.value}; current status is {actual}")


class TaskStateExperienceRetriever:
    """Fail-closed runtime adapter from structured TaskState fields to Store queries."""

    def __init__(self, store: ExperienceStore) -> None:
        self.store = store

    def retrieve(self, task_state: Mapping[str, Any]) -> tuple[Experience, ...]:
        raw_task = task_state.get("task")
        snapshot = raw_task if isinstance(raw_task, Mapping) else task_state
        raw_values = snapshot.get("values")
        values = raw_values if isinstance(raw_values, Mapping) else snapshot
        task_phase = str(values.get("task_state") or "").strip()
        runtime_surface = str(values.get("runtime_surface") or "").strip()
        failure_type = str(values.get("failure_type") or "").strip()
        if not failure_type:
            failures = snapshot.get("failures")
            if isinstance(failures, list) and failures and isinstance(failures[-1], Mapping):
                failure_type = str(failures[-1].get("error_type") or "").strip()
        if not task_phase or not runtime_surface or not failure_type:
            return ()
        return self.store.retrieve(
            RetrievalQuery(
                task_state=task_phase,
                failure_type=failure_type,
                runtime_surface=runtime_surface,
            )
        )
