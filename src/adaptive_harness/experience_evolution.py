"""Youtu-inspired offline group-relative Candidate Experience distillation."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from adaptive_harness.experience_store import Experience, ExperienceStore

_ALLOWED_EVENT_TYPES = {
    "assistant/message",
    "completion/checked",
    "failure/classified",
    "progress/checked",
    "recovery/decided",
    "recovery/executed",
    "recovery/outcome-evaluated",
    "resource/no-progress-checked",
    "tool/call",
    "tool/result",
}
_FORBIDDEN_KEY_PARTS = {
    "answer_key",
    "expected_answer",
    "expected_output",
    "ground_truth",
    "judge",
    "judge_reasoning",
    "private_verifier",
    "rubric",
    "verifier",
}
_FORBIDDEN_TEXT = re.compile(
    r"\b(?:expected[\s_-]+(?:answer|output)|ground[\s_-]+truth|"
    r"answer[\s_-]+key|gold[\s_-]+answer|rubric|verifier|private[\s_-]+verifier)\b",
    re.IGNORECASE,
)
_TASK_SPECIFIC_TEXT = re.compile(
    r"(?:\b(?:cli|browser|file|api)-[a-z0-9-]+\b|"
    r"#[a-zA-Z][\w-]*|\[data-[\w-]+\s*=|//[a-zA-Z])",
    re.IGNORECASE,
)


class RolloutPartition(StrEnum):
    DEVELOPMENT = "development"
    TRANSFER = "transfer"
    HELDOUT = "heldout"


@dataclass(frozen=True)
class DevelopmentRollout:
    task_id: str
    partition: RolloutPartition
    trajectory_ref: str
    task_state: str
    failure_type: str
    runtime_surface: str
    outcome_score: float
    passed: bool
    events: tuple[Mapping[str, Any], ...]

    def validate(self) -> None:
        text = (
            self.task_id,
            self.trajectory_ref,
            self.task_state,
            self.failure_type,
            self.runtime_surface,
        )
        if any(not item.strip() for item in text):
            raise ValueError("Development Rollout identity and trigger fields must be non-empty")
        if not 0.0 <= self.outcome_score <= 1.0:
            raise ValueError("Development Rollout outcome score must be between 0 and 1")


@dataclass(frozen=True)
class ExperienceGroupTrigger:
    task_state: str
    failure_type: str
    runtime_surface: str


@dataclass(frozen=True)
class SanitizedRollout:
    outcome_score: float
    passed: bool
    events: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ExperienceDraft:
    experience_id: str
    situation: str
    strategy: str
    anti_pattern: str
    progress_signal: str
    stop_condition: str


class ExperienceDistiller(Protocol):
    def distill(
        self,
        trigger: ExperienceGroupTrigger,
        rollouts: Sequence[SanitizedRollout],
    ) -> ExperienceDraft: ...


class TrajectoryLeakageError(ValueError):
    pass


class OfflineExperienceEvolution:
    """Create Candidates only; transfer validation and promotion remain separate."""

    def __init__(
        self,
        store: ExperienceStore,
        distiller: ExperienceDistiller,
        *,
        development_task_ids: Sequence[str],
    ) -> None:
        self.store = store
        self.distiller = distiller
        self.development_task_ids = frozenset(development_task_ids)
        if not self.development_task_ids:
            raise ValueError("trusted Development task set must be non-empty")

    def distill_candidates(
        self,
        rollouts: Sequence[DevelopmentRollout],
    ) -> tuple[Experience, ...]:
        for rollout in rollouts:
            rollout.validate()
            if rollout.partition is not RolloutPartition.DEVELOPMENT:
                raise ValueError("Offline distillation accepts only Development Rollouts")
            if rollout.task_id not in self.development_task_ids:
                raise ValueError(
                    f"Rollout task {rollout.task_id!r} is not in the trusted Development partition"
                )

        groups: dict[ExperienceGroupTrigger, list[DevelopmentRollout]] = defaultdict(list)
        for rollout in rollouts:
            groups[
                ExperienceGroupTrigger(
                    rollout.task_state,
                    rollout.failure_type,
                    rollout.runtime_surface,
                )
            ].append(rollout)

        prepared: list[Experience] = []
        for trigger in sorted(
            groups,
            key=lambda item: (item.task_state, item.failure_type, item.runtime_surface),
        ):
            group = groups[trigger]
            source_task_ids = tuple(sorted({rollout.task_id for rollout in group}))
            if len(source_task_ids) < 3:
                continue
            if len({rollout.outcome_score for rollout in group}) < 2:
                continue
            sanitized = tuple(
                _sanitize_rollout(rollout, source_task_ids)
                for rollout in sorted(
                    group,
                    key=lambda item: (item.outcome_score, item.passed, item.trajectory_ref),
                )
            )
            draft = self.distiller.distill(trigger, sanitized)
            experience = Experience(
                experience_id=draft.experience_id,
                version=1,
                task_state=trigger.task_state,
                failure_type=trigger.failure_type,
                runtime_surface=trigger.runtime_surface,
                situation=draft.situation,
                strategy=draft.strategy,
                anti_pattern=draft.anti_pattern,
                progress_signal=draft.progress_signal,
                stop_condition=draft.stop_condition,
                source_task_ids=source_task_ids,
            )
            experience.validate_candidate()
            prepared.append(experience)

        state = self.store.state
        existing_keys = set(state.experiences)
        existing_fingerprints = {
            experience.content_fingerprint for experience in state.experiences.values()
        }
        pending_keys: set[tuple[str, int]] = set()
        pending_fingerprints: set[str] = set()
        for experience in prepared:
            if experience.key in existing_keys or experience.key in pending_keys:
                raise ValueError("Experience candidate already exists")
            if (
                experience.content_fingerprint in existing_fingerprints
                or experience.content_fingerprint in pending_fingerprints
            ):
                raise ValueError("duplicate transferable Experience content")
            pending_keys.add(experience.key)
            pending_fingerprints.add(experience.content_fingerprint)

        for experience in prepared:
            self.store.create_candidate(experience)
        return tuple(prepared)


def _sanitize_rollout(
    rollout: DevelopmentRollout,
    source_task_ids: tuple[str, ...],
) -> SanitizedRollout:
    events = []
    for event in rollout.events:
        event_type = str(event.get("type") or "")
        if event_type not in _ALLOWED_EVENT_TYPES:
            raise TrajectoryLeakageError(
                f"trajectory event type is not admitted for distillation: {event_type!r}"
            )
        events.append(_sanitize_value(event, source_task_ids))
    return SanitizedRollout(rollout.outcome_score, rollout.passed, tuple(events))


def _sanitize_value(value: Any, source_task_ids: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        sanitized = {}
        for key, nested in value.items():
            normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key))
            normalized = normalized.casefold().replace("-", "_").replace(" ", "_")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise TrajectoryLeakageError(
                    f"trajectory contains evaluation-only field: {key!r}"
                )
            sanitized[str(key)] = _sanitize_value(nested, source_task_ids)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, source_task_ids) for item in value]
    if isinstance(value, str):
        if _FORBIDDEN_TEXT.search(value):
            raise TrajectoryLeakageError("trajectory contains evaluation-only text")
        sanitized_text = value
        for task_id in source_task_ids:
            sanitized_text = re.sub(re.escape(task_id), "[TASK_ID]", sanitized_text, flags=re.IGNORECASE)
        if _TASK_SPECIFIC_TEXT.search(sanitized_text):
            raise TrajectoryLeakageError(
                "trajectory contains task-specific identity or selector text"
            )
        return sanitized_text
    return value
