"""Replaceable offline Experience distillers with fail-closed model boundaries."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from adaptive_harness.experience_evolution import (
    ExperienceDraft,
    ExperienceGroupTrigger,
    SanitizedRollout,
)

_DRAFT_FIELDS = frozenset(
    {
        "experience_id",
        "situation",
        "strategy",
        "anti_pattern",
        "progress_signal",
        "stop_condition",
    }
)
_LEAKAGE_TEXT = re.compile(
    r"\b(?:expected[\s_-]+(?:answer|output)|ground[\s_-]+truth|"
    r"answer[\s_-]+key|gold[\s_-]+answer|rubric|judge[\s_-]+reasoning|"
    r"private[\s_-]+verifier|verifier)\b",
    re.IGNORECASE,
)
_TASK_OR_SELECTOR_TEXT = re.compile(
    r"(?:\b(?:cli|browser|file|api|dev|task|transfer|heldout|rrb|realreplica)-[a-z0-9-]+\b|"
    r"#[a-zA-Z][\w-]*|\[data-[\w-]+\s*=|//[a-zA-Z])",
    re.IGNORECASE,
)
_ID_TEXT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class DistillerSchemaError(ValueError):
    """Raised when a distiller output is malformed or leaks task/evaluator details."""


@dataclass(frozen=True)
class DistillerConfig:
    """Explicit opt-in switch for model-backed distillation."""

    enabled: bool = False


CompletionCallable = Callable[
    [ExperienceGroupTrigger, tuple[SanitizedRollout, ...]],
    str | Mapping[str, Any],
]


class DeterministicFakeDistiller:
    """Stable no-model distiller for tests, local gates, and dry-run development."""

    def distill(
        self,
        trigger: ExperienceGroupTrigger,
        rollouts: Sequence[SanitizedRollout],
    ) -> ExperienceDraft:
        if not rollouts:
            raise ValueError("fake distiller requires at least one sanitized rollout")
        failure = _slug(trigger.failure_type)
        state = _slug(trigger.task_state)
        surface = _slug(trigger.runtime_surface)
        outcome_scores = tuple(rollout.outcome_score for rollout in rollouts)
        has_success = any(rollout.passed for rollout in rollouts)
        has_failure = any(not rollout.passed for rollout in rollouts)
        contrast = (
            "after contrasting successful and failed attempts"
            if has_success and has_failure
            else "from comparable attempts"
        )
        score_span = max(outcome_scores) - min(outcome_scores)
        return ExperienceDraft(
            experience_id=f"{state}-{failure}-{surface}-bounded-recovery",
            situation=(
                f"A {trigger.runtime_surface} task is in {trigger.task_state} "
                f"after a {trigger.failure_type} signal."
            ),
            strategy=(
                f"Use the {contrast}: gather one new piece of evidence, narrow scope, "
                "then retry with an observable checkpoint."
            ),
            anti_pattern="Do not repeat an unchanged action after the same failure signal.",
            progress_signal=(
                "The next action changes visible state, returns new evidence, or narrows the failure "
                f"classification; observed outcome spread was {score_span:.2f}."
            ),
            stop_condition="Stop or switch recovery mode after a scoped retry also shows no new progress.",
        )


class ConstrainedModelDistiller:
    """Model-backed adapter whose only side effect is an injected completion call."""

    def __init__(
        self,
        complete: CompletionCallable,
        *,
        config: DistillerConfig | None = None,
    ) -> None:
        self._complete = complete
        self.config = config or DistillerConfig()

    def distill(
        self,
        trigger: ExperienceGroupTrigger,
        rollouts: Sequence[SanitizedRollout],
    ) -> ExperienceDraft:
        if not self.config.enabled:
            raise RuntimeError("model distiller is disabled; set DistillerConfig(enabled=True)")
        frozen_rollouts = tuple(rollouts)
        payload = self._complete(trigger, frozen_rollouts)
        return experience_draft_from_model_payload(payload)


def experience_draft_from_model_payload(payload: str | Mapping[str, Any]) -> ExperienceDraft:
    parsed = _parse_payload(payload)
    keys = set(parsed)
    extra = keys - _DRAFT_FIELDS
    missing = _DRAFT_FIELDS - keys
    if extra or missing:
        problems = []
        if extra:
            problems.append(f"extra fields: {sorted(extra)}")
        if missing:
            problems.append(f"missing fields: {sorted(missing)}")
        raise DistillerSchemaError("ExperienceDraft schema mismatch: " + "; ".join(problems))

    values: dict[str, str] = {}
    for field in sorted(_DRAFT_FIELDS):
        value = parsed[field]
        if not isinstance(value, str) or not value.strip():
            raise DistillerSchemaError(f"ExperienceDraft field {field!r} must be a non-empty string")
        normalized = value.strip()
        _reject_leakage(field, normalized)
        values[field] = normalized

    if not _ID_TEXT.match(values["experience_id"]):
        raise DistillerSchemaError("experience_id must be a lowercase slug")
    return ExperienceDraft(
        experience_id=values["experience_id"],
        situation=values["situation"],
        strategy=values["strategy"],
        anti_pattern=values["anti_pattern"],
        progress_signal=values["progress_signal"],
        stop_condition=values["stop_condition"],
    )


def _parse_payload(payload: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DistillerSchemaError("model distiller returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise DistillerSchemaError("model distiller must return a JSON object")
        return decoded
    if isinstance(payload, Mapping):
        return payload
    raise DistillerSchemaError("model distiller must return a mapping or JSON object string")


def _reject_leakage(field: str, value: str) -> None:
    if _LEAKAGE_TEXT.search(value):
        raise DistillerSchemaError(
            f"ExperienceDraft field {field!r} contains evaluator/answer leakage"
        )
    if _TASK_OR_SELECTOR_TEXT.search(value):
        raise DistillerSchemaError(
            f"ExperienceDraft field {field!r} contains task identity or selector leakage"
        )


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "unknown"
