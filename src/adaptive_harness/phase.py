"""Evidence-driven soft phase decisions for one task run.

The controller summarizes contract evidence into advisory runtime phase state.
It never uses task identifiers, benchmark labels, or assistant prose, and its
output is intentionally soft: downstream adapters may use the action intents and
budget semantics as guidance without treating them as hard workflow gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from adaptive_harness.task_contract import Criterion, CriterionKind
from adaptive_harness.task_state import CriterionAssessment, CriterionStatus, RuleBasedContractChecker, TaskState

PHASE_EVALUATED = "phase/evaluated"


class Phase(StrEnum):
    UNCONTRACTED = "uncontracted"
    CONTRACTED = "contracted"
    ACQUIRING = "acquiring"
    SYNTHESIZING = "synthesizing"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    READY = "ready"


@dataclass(frozen=True)
class PhaseDecision:
    phase: Phase
    unmet_obligation_ids: tuple[str, ...]
    reason: str
    action_intents: tuple[str, ...]
    budget_semantics: str
    unmet_obligations: tuple[dict[str, object], ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "unmet_obligation_ids": list(self.unmet_obligation_ids),
            "reason": self.reason,
            "action_intents": list(self.action_intents),
            "budget_semantics": self.budget_semantics,
            "unmet_obligations": [dict(item) for item in self.unmet_obligations],
        }


class PhaseController(Protocol):
    def evaluate(self, state: TaskState) -> PhaseDecision: ...


class RuleBasedPhaseController:
    """Project latest contract assessments into an advisory task phase."""

    def evaluate(self, state: TaskState) -> PhaseDecision:
        contract = state.contract
        if contract is None:
            return PhaseDecision(
                Phase.UNCONTRACTED,
                (),
                "No task contract has been created yet.",
                ("compile_contract",),
                "Spend only enough budget to derive verifiable public obligations.",
            )

        required_by_id = {criterion.id: criterion for criterion in contract.criteria if criterion.required}
        artifact_ids = {
            criterion.id
            for criterion in required_by_id.values()
            if criterion.kind is CriterionKind.ARTIFACT_EXISTS
        }
        completion = state.latest_completion or RuleBasedContractChecker().check(state)
        if not completion.assessments:
            unmet = tuple(required_by_id) or completion.missing
            return PhaseDecision(
                Phase.CONTRACTED,
                unmet,
                "Task contract exists but has no evidence assessment yet.",
                ("assess_contract", "observe"),
                "Take one bounded assessment step before planning more work.",
                (),
            )

        relevant = tuple(
            assessment
            for assessment in completion.assessments
            if assessment.criterion_id in required_by_id
        )
        unmet = tuple(
            assessment.criterion_id
            for assessment in relevant
            if assessment.status is not CriterionStatus.SATISFIED
        )
        unmet_obligations = _summarize_unmet(required_by_id, relevant)
        if required_by_id and len(relevant) == len(required_by_id) and not unmet:
            return PhaseDecision(
                Phase.READY,
                (),
                "All required contract obligations are satisfied.",
                ("deliver",),
                "Stop spending task budget except for final delivery bookkeeping.",
                (),
            )

        artifacts = tuple(
            assessment
            for assessment in relevant
            if _criterion(required_by_id, assessment).kind is CriterionKind.ARTIFACT_EXISTS
        )
        validations = tuple(
            assessment
            for assessment in relevant
            if _is_artifact_validation(_criterion(required_by_id, assessment), artifact_ids)
        )
        source_obligations = tuple(
            assessment
            for assessment in relevant
            if _is_source_obligation(_criterion(required_by_id, assessment), artifact_ids)
        )

        if any(assessment.status is CriterionStatus.UNSATISFIED for assessment in validations):
            return PhaseDecision(
                Phase.REPAIRING,
                unmet,
                "Artifact shape or validation evidence is explicitly unsatisfied.",
                ("repair_artifact", "validate_contract"),
                "Repair the failed artifact validation with a bounded novel action; avoid no-progress retries.",
                unmet_obligations,
            )

        unsatisfied_source = tuple(
            assessment
            for assessment in source_obligations
            if assessment.status is CriterionStatus.UNSATISFIED
        )
        if unsatisfied_source:
            return PhaseDecision(
                Phase.REPAIRING,
                unmet,
                "A source or runtime observation contradicts a required public obligation.",
                ("repair_state", "validate_contract"),
                "Repair only the contradicted obligation and avoid broad replanning.",
                unmet_obligations,
            )

        pending_source = tuple(
            assessment
            for assessment in source_obligations
            if assessment.status in {CriterionStatus.PENDING, CriterionStatus.BLOCKED}
        )
        if pending_source:
            return PhaseDecision(
                Phase.ACQUIRING,
                unmet,
                "source or runtime observation obligations are still pending.",
                ("observe", "inspect", "gather_evidence"),
                "Prefer novel evidence acquisition over synthesis; stop repeated no-progress probes.",
                unmet_obligations,
            )

        artifact_not_satisfied = tuple(
            assessment for assessment in artifacts if assessment.status is not CriterionStatus.SATISFIED
        )
        if artifact_not_satisfied:
            return PhaseDecision(
                Phase.SYNTHESIZING,
                unmet,
                "Required artifact evidence is still missing.",
                ("write_artifact", "synthesize"),
                "Spend budget on producing the deliverable once, then validate immediately.",
                unmet_obligations,
            )

        if any(assessment.status in {CriterionStatus.PENDING, CriterionStatus.BLOCKED} for assessment in validations):
            return PhaseDecision(
                Phase.VALIDATING,
                unmet,
                "Artifact exists but artifact validation evidence is pending.",
                ("inspect_artifact", "validate_contract"),
                "Spend a bounded validation step before further edits.",
                unmet_obligations,
            )

        return PhaseDecision(
            Phase.CONTRACTED,
            unmet or completion.missing,
            "Contract evidence is incomplete but does not identify a more specific phase.",
            ("assess_contract",),
            "Spend only enough budget to produce a more specific evidence state.",
            unmet_obligations,
        )


def _criterion(criteria: dict[str, Criterion], assessment: CriterionAssessment) -> Criterion:
    return criteria[assessment.criterion_id]


def _is_artifact_validation(criterion: Criterion, artifact_ids: set[str]) -> bool:
    subject = str(criterion.parameters.get("subject") or "")
    return (
        criterion.kind is CriterionKind.EXACT_COUNT
        or (criterion.kind is CriterionKind.OBSERVATION_EQUALS and subject.startswith("artifact."))
        or any(dependency in artifact_ids for dependency in criterion.depends_on)
    )


def _is_source_obligation(criterion: Criterion, artifact_ids: set[str]) -> bool:
    return (
        criterion.kind is not CriterionKind.ARTIFACT_EXISTS
        and not _is_artifact_validation(criterion, artifact_ids)
    )


def _summarize_unmet(
    criteria: dict[str, Criterion],
    assessments: tuple[CriterionAssessment, ...],
) -> tuple[dict[str, object], ...]:
    summaries: list[dict[str, object]] = []
    for assessment in assessments:
        if assessment.status is CriterionStatus.SATISFIED or assessment.criterion_id not in criteria:
            continue
        criterion = criteria[assessment.criterion_id]
        summary: dict[str, object] = {
            "criterion_id": criterion.id,
            "description": criterion.description,
            "kind": criterion.kind.value,
            "status": assessment.status.value,
        }
        resource = _public_resource(criterion.parameters)
        if resource is not None:
            summary["resource"] = resource
        summaries.append(summary)
    return tuple(summaries)


def _public_resource(parameters: Any) -> str | None:
    if not isinstance(parameters, Mapping):
        return None
    raw = parameters.get("resource")
    if isinstance(raw, str) and raw.startswith(("http://", "https://")):
        return raw
    path = parameters.get("path")
    if isinstance(path, str) and path and not path.startswith(("/", "~")) and ":" not in path:
        return path
    return None
