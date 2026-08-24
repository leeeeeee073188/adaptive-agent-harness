"""Progress-based execution guardrail; token volume is diagnostic only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NoProgressDisposition(StrEnum):
    ALLOW = "allow"
    RECORD = "record"
    REPLAN = "replan"
    BLOCK_SCOPE = "block_scope"


@dataclass(frozen=True)
class GuardrailObservation:
    action_scope: str
    strategy_fingerprint: str
    mutation_epoch: int
    semantic_progress: bool
    post_mutation_verification: bool = False

    def __post_init__(self) -> None:
        if not self.action_scope.strip() or not self.strategy_fingerprint.strip():
            raise ValueError("action scope and strategy fingerprint must be non-empty")
        if self.mutation_epoch < 0:
            raise ValueError("mutation epoch must be non-negative")


@dataclass(frozen=True)
class NoProgressDecision:
    disposition: NoProgressDisposition
    consecutive_no_progress: int
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "consecutive_no_progress": self.consecutive_no_progress,
            "reason": self.reason,
        }


class ResourceGuardrail:
    """Escalate repeated same-scope strategies that produce no task progress."""

    def __init__(self) -> None:
        self._mutation_epoch: int | None = None
        self._streaks: dict[tuple[str, str], int] = {}
        self._verified_after_mutation: set[tuple[int, str]] = set()

    def observe(self, observation: GuardrailObservation) -> NoProgressDecision:
        if self._mutation_epoch is not None and observation.mutation_epoch < self._mutation_epoch:
            raise ValueError("mutation epoch cannot move backwards")
        if self._mutation_epoch != observation.mutation_epoch:
            self._mutation_epoch = observation.mutation_epoch
            self._streaks.clear()
            self._verified_after_mutation.clear()
            if observation.post_mutation_verification:
                self._verified_after_mutation.add(
                    (observation.mutation_epoch, observation.action_scope)
                )
                return NoProgressDecision(
                    NoProgressDisposition.ALLOW,
                    0,
                    "First verification after a successful mutation is allowed.",
                )

        if observation.semantic_progress:
            self._streaks.clear()
            return NoProgressDecision(
                NoProgressDisposition.ALLOW,
                0,
                "Semantic task evidence or world state progressed.",
            )

        verification_key = (observation.mutation_epoch, observation.action_scope)
        if (
            observation.post_mutation_verification
            and verification_key not in self._verified_after_mutation
        ):
            self._verified_after_mutation.add(verification_key)
            return NoProgressDecision(
                NoProgressDisposition.ALLOW,
                0,
                "First verification for this scope after mutation is allowed.",
            )

        signature = (observation.action_scope, observation.strategy_fingerprint)
        count = self._streaks.get(signature, 0) + 1
        self._streaks[signature] = count
        if count == 1:
            disposition = NoProgressDisposition.RECORD
            reason = "No-progress event recorded; one observation is not enough to intervene."
        elif count == 2:
            disposition = NoProgressDisposition.REPLAN
            reason = "Repeated no-progress strategy must replan before another equivalent action."
        else:
            disposition = NoProgressDisposition.BLOCK_SCOPE
            reason = "Third no-progress strategy blocks this action scope until strategy or state changes."
        return NoProgressDecision(disposition, count, reason)
