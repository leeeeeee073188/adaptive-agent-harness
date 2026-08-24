from __future__ import annotations

import unittest

from adaptive_harness.ledger import SessionLedger
from adaptive_harness.phase import Phase, RuleBasedPhaseController
from adaptive_harness.task_contract import Criterion, CriterionKind, CriterionSource, TaskContract
from adaptive_harness.task_state import (
    ContractCompletionResult,
    CriterionAssessment,
    CriterionStatus,
    Evidence,
    EvidenceKind,
    EvidenceSource,
    TaskEventWriter,
    TaskState,
    TaskStateProjector,
)


def _contract() -> TaskContract:
    return TaskContract(
        "phase-task",
        "Write and validate outputs/audit.json.",
        (
            Criterion(
                "artifact:outputs-audit-json",
                "Artifact exists",
                CriterionKind.ARTIFACT_EXISTS,
                CriterionSource.TASK_PROMPT,
                {"path": "outputs/audit.json"},
            ),
            Criterion(
                "shape:outputs-audit-json",
                "Artifact JSON shape is valid",
                CriterionKind.OBSERVATION_EQUALS,
                CriterionSource.TASK_PROMPT,
                {
                    "subject": "artifact.json_shape:outputs/audit.json",
                    "path": "outputs/audit.json",
                    "expected": True,
                },
                depends_on=("artifact:outputs-audit-json",),
            ),
            Criterion(
                "source:records",
                "Runtime source observation is available",
                CriterionKind.OBSERVATION_EQUALS,
                CriterionSource.RUNTIME_OBSERVATION,
                {"subject": "source.records_loaded", "expected": True},
            ),
            Criterion(
                "count:records",
                "Artifact record count is valid",
                CriterionKind.EXACT_COUNT,
                CriterionSource.TASK_PROMPT,
                {"subject": "records", "expected": 3},
                depends_on=("artifact:outputs-audit-json",),
            ),
            Criterion(
                "observation-with-path",
                "Generic runtime observation that mentions a path",
                CriterionKind.OBSERVATION_EQUALS,
                CriterionSource.RUNTIME_OBSERVATION,
                {"subject": "source.sidecar", "path": "outputs/audit.json", "expected": True},
            ),
        ),
    )


def _state_with(*assessments: CriterionAssessment) -> object:
    ledger = SessionLedger("phase-unit")
    writer = TaskEventWriter(ledger)
    writer.create_contract(_contract())
    result = ContractCompletionResult(
        all(item.status is CriterionStatus.SATISFIED for item in assessments),
        tuple(assessments),
        tuple(item.criterion_id for item in assessments if item.status is not CriterionStatus.SATISFIED),
        "phase fixture",
    )
    ledger.append("completion/checked", result.to_payload())
    return TaskStateProjector().project(ledger.events)


def _source_access_contract(*, with_shape: bool = False) -> TaskContract:
    criteria = [
        Criterion(
            "artifact:outputs-audit-json",
            "Artifact exists",
            CriterionKind.ARTIFACT_EXISTS,
            CriterionSource.TASK_PROMPT,
            {"path": "outputs/audit.json"},
        ),
        Criterion(
            "source-access:public-help",
            "Public source URL must be read",
            CriterionKind.OBSERVATION_EQUALS,
            CriterionSource.TASK_PROMPT,
            {
                "subject": "source.access:https://public.example.test/api/help",
                "resource": "https://public.example.test/api/help",
                "expected": True,
                "private_note": "must-not-leak",
            },
        ),
    ]
    if with_shape:
        criteria.append(
            Criterion(
                "shape:outputs-audit-json",
                "Artifact JSON shape is valid",
                CriterionKind.OBSERVATION_EQUALS,
                CriterionSource.TASK_PROMPT,
                {
                    "subject": "artifact.json_shape:outputs/audit.json",
                    "path": "outputs/audit.json",
                    "expected": True,
                },
                depends_on=("artifact:outputs-audit-json",),
            )
        )
    return TaskContract("source-access-phase", "Read source and write artifact.", tuple(criteria))


def _state_for(contract: TaskContract, *assessments: CriterionAssessment) -> object:
    ledger = SessionLedger("phase-custom")
    writer = TaskEventWriter(ledger)
    writer.create_contract(contract)
    if assessments:
        result = ContractCompletionResult(
            all(item.status is CriterionStatus.SATISFIED for item in assessments),
            tuple(assessments),
            tuple(item.criterion_id for item in assessments if item.status is not CriterionStatus.SATISFIED),
            "phase fixture",
        )
        ledger.append("completion/checked", result.to_payload())
    return TaskStateProjector().project(ledger.events)


def _state_for_with_evidence(contract: TaskContract, *evidence: Evidence) -> object:
    ledger = SessionLedger("phase-evidence")
    writer = TaskEventWriter(ledger)
    writer.create_contract(contract)
    for item in evidence:
        writer.add_evidence(item)
    return TaskStateProjector().project(ledger.events)


class RuleBasedPhaseControllerTests(unittest.TestCase):

    def test_no_completion_with_before_run_source_evidence_routes_to_synthesizing(self) -> None:
        state = _state_for_with_evidence(
            _source_access_contract(),
            Evidence(
                "source-before-run",
                EvidenceKind.OBSERVATION,
                "source.access:https://public.example.test/api/help",
                True,
                EvidenceSource.RUNTIME_OBSERVATION,
                {
                    "criterion_id": "source-access:public-help",
                    "resource": "https://public.example.test/api/help",
                },
            ),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.SYNTHESIZING)
        rendered = str(decision.unmet_obligations)
        self.assertIn("Artifact exists", rendered)
        self.assertNotIn("Public source URL must be read", rendered)

    def test_artifact_only_without_completion_routes_to_synthesizing(self) -> None:
        contract = TaskContract(
            "artifact-only",
            "Write artifact.",
            (
                Criterion(
                    "artifact:outputs-audit-json",
                    "Artifact exists",
                    CriterionKind.ARTIFACT_EXISTS,
                    CriterionSource.TASK_PROMPT,
                    {"path": "outputs/audit.json"},
                ),
            ),
        )
        state = _state_for(contract)

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.SYNTHESIZING)

    def test_no_assessment_with_source_access_routes_to_acquiring_with_public_url_summary(self) -> None:
        state = _state_for(_source_access_contract())

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.ACQUIRING)
        rendered = str(decision.unmet_obligations)
        self.assertIn("Public source URL must be read", rendered)
        self.assertIn("observation_equals", rendered)
        self.assertIn("pending", rendered)
        self.assertIn("https://public.example.test/api/help", rendered)
        self.assertNotIn("must-not-leak", rendered)

    def test_source_pending_overrides_unsatisfied_artifact_exists_to_acquiring(self) -> None:
        state = _state_for(
            _source_access_contract(),
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.UNSATISFIED, "file missing"),
            CriterionAssessment("source-access:public-help", CriterionStatus.PENDING, "URL not read"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.ACQUIRING)
        self.assertIn("source-access:public-help", decision.unmet_obligation_ids)

    def test_unsatisfied_artifact_shape_routes_to_repairing_even_with_source_satisfied(self) -> None:
        state = _state_for(
            _source_access_contract(with_shape=True),
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.SATISFIED, "exists"),
            CriterionAssessment("source-access:public-help", CriterionStatus.SATISFIED, "URL read"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.UNSATISFIED, "shape false"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.REPAIRING)

    def test_contradicted_runtime_observation_routes_to_repairing(self) -> None:
        contract = TaskContract(
            "runtime-state",
            "Observe public state.",
            (
                Criterion(
                    "state:ready",
                    "Public state must be ready",
                    CriterionKind.OBSERVATION_EQUALS,
                    CriterionSource.TASK_PROMPT,
                    {"subject": "state.ready", "expected": True},
                ),
            ),
        )
        state = _state_for(
            contract,
            CriterionAssessment("state:ready", CriterionStatus.UNSATISFIED, "observed false"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.REPAIRING)

    def test_unsatisfied_artifact_without_source_routes_to_synthesizing(self) -> None:
        contract = TaskContract(
            "artifact-only",
            "Write artifact.",
            (
                Criterion(
                    "artifact:outputs-audit-json",
                    "Artifact exists",
                    CriterionKind.ARTIFACT_EXISTS,
                    CriterionSource.TASK_PROMPT,
                    {"path": "outputs/audit.json"},
                ),
            ),
        )
        state = _state_for(
            contract,
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.UNSATISFIED, "file missing"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.SYNTHESIZING)

    def test_routes_uncontracted_state_to_uncontracted(self) -> None:
        decision = RuleBasedPhaseController().evaluate(TaskState())

        self.assertEqual(decision.phase, Phase.UNCONTRACTED)
        self.assertIn("compile_contract", decision.action_intents)

    def test_routes_pending_source_observation_to_acquiring(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.PENDING, "missing"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.BLOCKED, "blocked"),
            CriterionAssessment("source:records", CriterionStatus.PENDING, "need source"),
            CriterionAssessment("count:records", CriterionStatus.BLOCKED, "blocked"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.ACQUIRING)
        self.assertEqual(
            decision.unmet_obligation_ids,
            ("artifact:outputs-audit-json", "shape:outputs-audit-json", "source:records", "count:records"),
        )
        self.assertIn("source", decision.reason)
        self.assertIn("observe", decision.action_intents)

    def test_routes_missing_artifact_after_non_artifact_evidence_to_synthesizing(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.PENDING, "missing"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.BLOCKED, "blocked"),
            CriterionAssessment("source:records", CriterionStatus.SATISFIED, "observed"),
            CriterionAssessment("count:records", CriterionStatus.PENDING, "count waits for artifact"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.SYNTHESIZING)
        self.assertIn("write_artifact", decision.action_intents)

    def test_routes_existing_artifact_with_pending_shape_to_validating(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.SATISFIED, "exists"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.PENDING, "not inspected"),
            CriterionAssessment("source:records", CriterionStatus.SATISFIED, "observed"),
            CriterionAssessment("count:records", CriterionStatus.PENDING, "not counted"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.VALIDATING)
        self.assertIn("validate_contract", decision.action_intents)

    def test_generic_observation_with_path_is_source_not_artifact_shape(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.SATISFIED, "exists"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.SATISFIED, "valid"),
            CriterionAssessment("source:records", CriterionStatus.SATISFIED, "observed"),
            CriterionAssessment("count:records", CriterionStatus.SATISFIED, "counted"),
            CriterionAssessment("observation-with-path", CriterionStatus.PENDING, "need source sidecar"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.ACQUIRING)
        self.assertIn("observation-with-path", decision.unmet_obligation_ids)

    def test_routes_unsatisfied_shape_to_repairing(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.SATISFIED, "exists"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.UNSATISFIED, "bad shape"),
            CriterionAssessment("source:records", CriterionStatus.SATISFIED, "observed"),
            CriterionAssessment("count:records", CriterionStatus.SATISFIED, "counted"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.REPAIRING)
        self.assertIn("shape:outputs-audit-json", decision.unmet_obligation_ids)

    def test_routes_all_satisfied_to_ready(self) -> None:
        state = _state_with(
            CriterionAssessment("artifact:outputs-audit-json", CriterionStatus.SATISFIED, "exists"),
            CriterionAssessment("shape:outputs-audit-json", CriterionStatus.SATISFIED, "valid"),
            CriterionAssessment("source:records", CriterionStatus.SATISFIED, "observed"),
            CriterionAssessment("count:records", CriterionStatus.SATISFIED, "counted"),
            CriterionAssessment("observation-with-path", CriterionStatus.SATISFIED, "observed"),
        )

        decision = RuleBasedPhaseController().evaluate(state)  # type: ignore[arg-type]

        self.assertEqual(decision.phase, Phase.READY)
        self.assertEqual(decision.unmet_obligation_ids, ())
        self.assertIn("deliver", decision.action_intents)


if __name__ == "__main__":
    unittest.main()
