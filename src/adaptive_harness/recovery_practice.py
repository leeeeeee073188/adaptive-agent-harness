"""Offline, confidence-gated statistics for recovery outcomes."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from adaptive_harness.recovery import RecoveryOutcome, TaskRecoveryAction


@dataclass(frozen=True)
class ActionOutcomeStats:
    action: TaskRecoveryAction
    total_samples: int
    isolated_samples: int
    confounded_samples: int
    isolated_effective: int
    effective_rate: float | None
    wilson_lower: float | None
    wilson_upper: float | None


@dataclass(frozen=True)
class RecoveryPracticeDecision:
    action: TaskRecoveryAction
    eligible: bool
    stats: ActionOutcomeStats
    reason: str


class RecoveryOutcomeAggregator:
    """Keep multi-action outcomes visible but out of causal promotion counts."""

    def aggregate(self, outcomes: Iterable[RecoveryOutcome]) -> tuple[ActionOutcomeStats, ...]:
        total: dict[TaskRecoveryAction, int] = defaultdict(int)
        isolated: dict[TaskRecoveryAction, list[bool]] = defaultdict(list)
        confounded: dict[TaskRecoveryAction, int] = defaultdict(int)
        for outcome in outcomes:
            unique_actions = tuple(dict.fromkeys(outcome.actions))
            for action in unique_actions:
                total[action] += 1
                if len(unique_actions) == 1:
                    isolated[action].append(outcome.effective)
                else:
                    confounded[action] += 1
        rows = []
        for action in sorted(total, key=lambda item: item.value):
            samples = isolated[action]
            successes = sum(samples)
            lower, upper = wilson_interval(successes, len(samples))
            rows.append(
                ActionOutcomeStats(
                    action,
                    total[action],
                    len(samples),
                    confounded[action],
                    successes,
                    successes / len(samples) if samples else None,
                    lower,
                    upper,
                )
            )
        return tuple(rows)


class RecoveryPracticeGate:
    def __init__(
        self,
        *,
        min_isolated_samples: int = 5,
        min_effective_rate: float = 0.6,
        min_wilson_lower: float = 0.3,
    ) -> None:
        if min_isolated_samples < 1:
            raise ValueError("min_isolated_samples must be positive")
        self.min_isolated_samples = min_isolated_samples
        self.min_effective_rate = min_effective_rate
        self.min_wilson_lower = min_wilson_lower

    def evaluate(self, stats: ActionOutcomeStats) -> RecoveryPracticeDecision:
        if stats.isolated_samples < self.min_isolated_samples:
            return RecoveryPracticeDecision(
                stats.action,
                False,
                stats,
                "Insufficient isolated outcomes; confounded batches do not count.",
            )
        if (
            stats.effective_rate is None
            or stats.effective_rate < self.min_effective_rate
            or stats.wilson_lower is None
            or stats.wilson_lower < self.min_wilson_lower
        ):
            return RecoveryPracticeDecision(
                stats.action,
                False,
                stats,
                "Observed effectiveness or confidence lower bound is below threshold.",
            )
        return RecoveryPracticeDecision(
            stats.action,
            True,
            stats,
            "Enough isolated effective outcomes to admit offline practice evidence.",
        )


def wilson_interval(successes: int, samples: int, *, z: float = 1.96) -> tuple[float | None, float | None]:
    if samples == 0:
        return None, None
    proportion = successes / samples
    denominator = 1 + z**2 / samples
    center = (proportion + z**2 / (2 * samples)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / samples
            + z**2 / (4 * samples**2)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)
