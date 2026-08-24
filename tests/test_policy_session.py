from __future__ import annotations

import unittest

from adaptive_harness.capabilities import AcceptFinalCompletion, ModelResponse
from adaptive_harness.context import ContextBudget, TaskAwareContextManager
from adaptive_harness.experience_store import (
    Experience,
    ExperienceStore,
    TaskStateExperienceRetriever,
    TransferValidation,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.phase import PHASE_EVALUATED, Phase
from adaptive_harness.policy_session import KernelPolicySession, bind_policy_session, current_policy_task_state
from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
    TaskFailureCategory,
    TaskRecoveryAction,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.task_state import (
    CriterionStatus,
    Evidence,
    EvidenceCompletionGate,
    EvidenceKind,
    EvidenceSource,
    TaskEventWriter,
    TaskStateProjector,
)


class PolicySessionTests(unittest.TestCase):
    def test_provisional_copy_routes_to_synthesis_lineage_recovery(self) -> None:
        ledger = SessionLedger("policy-grounding-copy")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        contract = session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt=(
                "`workspace/results.json` is a draft, not truth. Re-check it against raw "
                "records before writing outputs/report.json."
            ),
            public_schema=None,
        )
        grounding = next(
            item
            for item in contract.criteria
            if str(item.parameters.get("subject") or "").startswith("artifact.grounding:")
        )
        writer = TaskEventWriter(ledger)
        writer.add_evidence(
            Evidence(
                "artifact",
                EvidenceKind.ARTIFACT,
                "outputs/report.json",
                {"exists": True, "sha256": "a" * 64},
                EvidenceSource.ARTIFACT_INSPECTION,
            )
        )
        writer.add_evidence(
            Evidence(
                "grounding",
                EvidenceKind.OBSERVATION,
                str(grounding.parameters["subject"]),
                False,
                EvidenceSource.ARTIFACT_INSPECTION,
                {
                    "diagnostics": [
                        "artifact exactly copies provisional source requiring validation: "
                        "workspace/results.json"
                    ]
                },
            )
        )

        result, feedback, recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIn("exactly copies provisional source", feedback or "")
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertEqual(
            recovery.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN),
        )
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(
            state.recoveries[-1].primary,
            TaskFailureCategory.SYNTHESIS_LINEAGE_GAP,
        )

    def test_blocked_grounding_criterion_does_not_hide_missing_artifact_recovery(self) -> None:
        ledger = SessionLedger("policy-grounding-before-artifact")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt=(
                "`workspace/results.json` is a draft, not truth. Re-check it against raw "
                "records before writing outputs/report.json."
            ),
            public_schema=None,
        )

        result, feedback, recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertIn(TaskRecoveryAction.WRITE_PARTIAL, recovery.actions)
        self.assertIn("[HARNESS DELIVERY REQUIRED]", feedback or "")
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.recoveries[-1].primary, TaskFailureCategory.ARTIFACT_ERROR)

    def test_response_policy_rejects_runtime_limit_text_as_completion(self) -> None:
        policy = AcceptFinalCompletion()

        decision = policy.check(
            "Complete the task.",
            ModelResponse(content="Tool call limit reached: run limit exceeded (21/20 calls)."),
            {},
        )

        self.assertFalse(decision.passed)
        self.assertIn("runtime control", decision.feedback or "")

    def test_response_policy_accepts_legitimate_non_empty_final_response(self) -> None:
        policy = AcceptFinalCompletion()

        decision = policy.check(
            "Complete the task.",
            ModelResponse(content="Completed the requested artifact and validated its contents."),
            {},
        )

        self.assertTrue(decision.passed)

    def test_rejected_runtime_final_preserves_evidence_assessments_for_recovery(self) -> None:
        ledger = SessionLedger("policy-runtime-final-with-missing-artifact")
        session = KernelPolicySession(
            response_completion_policy=AcceptFinalCompletion(),
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.json.",
            public_schema=None,
        )

        result, feedback, recovery = session.check_completion(
            ledger,
            response=ModelResponse(
                content="Tool call limit reached: run limit exceeded (21/20 calls)."
            ),
        )

        self.assertFalse(result.passed)
        self.assertEqual(len(result.assessments), 1)
        self.assertIn("acceptable final response", result.missing)
        self.assertTrue(any("outputs/report.json" in item for item in result.missing))
        self.assertIn("runtime control", feedback or "")
        self.assertIn("outputs/report.json", feedback or "")
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertIn(TaskRecoveryAction.WRITE_PARTIAL, recovery.actions)
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.recoveries[-1].primary, TaskFailureCategory.ARTIFACT_ERROR)
        self.assertEqual(state.values["phase.current"], Phase.SYNTHESIZING.value)

    def test_response_and_evidence_completion_are_one_session_decision(self) -> None:
        ledger = SessionLedger("policy-response")
        session = KernelPolicySession(
            response_completion_policy=AcceptFinalCompletion(),
            completion_gate=None,
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Complete the public task.",
            public_schema=None,
        )

        rejected, feedback, _ = session.check_completion(
            ledger,
            response=ModelResponse(content=""),
        )

        self.assertFalse(rejected.passed)
        self.assertIn("non-empty final response", feedback or "")
        self.assertEqual(
            [event.type for event in ledger.events].count("completion/checked"),
            1,
        )

    def test_contract_and_policy_configuration_precede_completion(self) -> None:
        ledger = SessionLedger("policy-contract")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())

        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.csv.",
            public_schema=None,
        )
        result, _feedback, _recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        types = [event.type for event in ledger.events]
        self.assertLess(types.index("task/contract-created"), types.index("policy/configured"))
        self.assertLess(types.index("policy/configured"), types.index("completion/checked"))

    def test_recovery_decision_is_ledgered_before_execution(self) -> None:
        ledger = SessionLedger("policy-recovery")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.csv.",
            public_schema=None,
        )

        _result, feedback, recovery = session.check_completion(ledger)

        self.assertIsNotNone(recovery)
        self.assertIn(TaskRecoveryAction.VALIDATE_CONTRACT, recovery.actions)
        self.assertIn("Validate each missing contract criterion", feedback or "")
        types = [event.type for event in ledger.events]
        self.assertLess(types.index("recovery/decided"), types.index("recovery/executed"))
        state = TaskStateProjector().project(ledger.events)
        self.assertIn("recovery.missing_requirements", state.values)


    def test_completion_evaluates_soft_phase_and_state_once_per_decision(self) -> None:
        ledger = SessionLedger("policy-phase")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.csv.",
            public_schema=None,
        )

        first, _feedback, _recovery = session.check_completion(ledger)
        second, _feedback, _recovery = session.check_completion(ledger)

        self.assertFalse(first.passed)
        self.assertFalse(second.passed)
        phase_events = [event for event in ledger.events if event.type == PHASE_EVALUATED]
        self.assertEqual(len(phase_events), 1)
        self.assertEqual(phase_events[0].payload["phase"], Phase.SYNTHESIZING.value)
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.values["phase.current"], Phase.SYNTHESIZING.value)
        self.assertEqual(state.values["phase.unmet_obligation_ids"], ["artifact:outputs-report-csv"])
        self.assertEqual(state.values["phase.action_intents"], ["write_artifact", "synthesize"])
        self.assertIn("artifact evidence is still missing", state.values["phase.reason"])
        self.assertIn("validate immediately", state.values["phase.budget_semantics"])
        context = state.to_context()
        self.assertEqual(context["values"]["phase.current"], Phase.SYNTHESIZING.value)
        self.assertEqual(context["values"]["phase.action_intents"], ["write_artifact", "synthesize"])
        self.assertIn("artifact evidence is still missing", context["values"]["phase.reason"])
        self.assertIn("validate immediately", context["values"]["phase.budget_semantics"])
        self.assertEqual(context["values"]["phase.unmet_obligations"][0]["resource"], "outputs/report.csv")

    def test_begin_turn_with_source_access_contract_exposes_public_unmet_obligation(self) -> None:
        ledger = SessionLedger("policy-phase-source-access-begin")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Read https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )

        session.begin_turn(ledger)

        phase_events = [event for event in ledger.events if event.type == PHASE_EVALUATED]
        self.assertEqual(len(phase_events), 1)
        self.assertEqual(phase_events[0].payload["phase"], Phase.ACQUIRING.value)
        state = TaskStateProjector().project(ledger.events)
        context = state.to_context()
        unmet = context["values"]["phase.unmet_obligations"]
        rendered = str(unmet)
        self.assertIn("https://public.example.com/data", rendered)
        self.assertIn("observation_equals", rendered)
        self.assertIn("pending", rendered)
        self.assertNotIn("private", rendered.lower())

    def test_begin_turn_evaluates_artifact_only_phase_without_progress_detector(self) -> None:
        ledger = SessionLedger("policy-phase-begin")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.csv.",
            public_schema=None,
        )

        snapshot = session.begin_turn(ledger)

        self.assertIsNone(snapshot)
        phase_events = [event for event in ledger.events if event.type == PHASE_EVALUATED]
        self.assertEqual(len(phase_events), 1)
        self.assertEqual(phase_events[0].payload["phase"], Phase.SYNTHESIZING.value)
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.values["phase.current"], Phase.SYNTHESIZING.value)



    def test_missing_source_and_artifact_recovers_by_acquiring_evidence_without_delivery(self) -> None:
        ledger = SessionLedger("policy-source-gap-before-artifact")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Read https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )

        result, feedback, recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertEqual(
            recovery.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN),
        )
        self.assertNotIn("[HARNESS DELIVERY REQUIRED]", feedback or "")
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.recoveries[-1].primary, TaskFailureCategory.EVIDENCE_GAP)
        self.assertEqual(state.values["phase.current"], Phase.ACQUIRING.value)

    def test_after_source_evidence_missing_artifact_recovers_with_delivery(self) -> None:
        ledger = SessionLedger("policy-artifact-after-source")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Read https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )
        ledger.append(
            "tool/action-audited",
            {
                "record": {
                    "intent": "read",
                    "resources": ["https://public.example.com/data"],
                    "scope_key": "source",
                    "argument_fingerprint": "source",
                    "mutation_epoch": 0,
                },
                "decision": {"disposition": "allow"},
            },
            turn=1,
        )
        session.check_resources(ledger, progress=None, turn=1)

        result, feedback, recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertEqual(
            recovery.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.WRITE_PARTIAL),
        )
        self.assertIn("[HARNESS DELIVERY REQUIRED]", feedback or "")
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.recoveries[-1].primary, TaskFailureCategory.ARTIFACT_ERROR)
        self.assertEqual(state.values["phase.current"], Phase.SYNTHESIZING.value)

    def test_artifact_only_recovery_still_requests_delivery(self) -> None:
        ledger = SessionLedger("policy-artifact-only")
        session = KernelPolicySession(
            completion_gate=EvidenceCompletionGate(),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
        )
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.md.",
            public_schema=None,
        )

        result, feedback, recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIsNotNone(recovery)
        assert recovery is not None
        self.assertIn(TaskRecoveryAction.WRITE_PARTIAL, recovery.actions)
        self.assertIn("[HARNESS DELIVERY REQUIRED]", feedback or "")

    def test_source_access_evidence_from_audited_tool_resource_satisfies_contract(self) -> None:
        ledger = SessionLedger("policy-source-access")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Open https://public.example.com/data and write outputs/report.md.",
            public_schema=None,
        )
        ledger.append(
            "tool/action-audited",
            {
                "record": {
                    "intent": "read",
                    "resources": ["https://public.example.com/data"],
                    "scope_key": "source",
                    "argument_fingerprint": "source",
                    "mutation_epoch": 0,
                },
                "decision": {"disposition": "allow"},
            },
            turn=1,
        )

        session.check_resources(ledger, progress=None, turn=1)
        result, _feedback, _recovery = session.check_completion(ledger)

        source = next(item for item in result.assessments if item.criterion_id.startswith("observation:source-access"))
        self.assertEqual(source.status, CriterionStatus.SATISFIED)

    def test_completion_rejects_when_required_source_was_not_accessed(self) -> None:
        ledger = SessionLedger("policy-source-missing")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Read https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )

        result, feedback, _recovery = session.check_completion(ledger)

        self.assertFalse(result.passed)
        self.assertIn("https://public.example.com/data", feedback or "")

    def test_failed_source_tool_result_does_not_satisfy_access(self) -> None:
        ledger = SessionLedger("policy-source-failed")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Read https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )
        ledger.append(
            "tool/action-audited",
            {
                "record": {
                    "intent": "read",
                    "resources": ["https://public.example.com/data"],
                    "error_type": "TOOL_ERROR",
                    "scope_key": "source",
                    "argument_fingerprint": "source",
                    "mutation_epoch": 0,
                },
                "decision": {"disposition": "allow"},
            },
            turn=1,
        )

        session.check_resources(ledger, progress=None, turn=1)
        result, _feedback, _recovery = session.check_completion(ledger)

        source = next(
            item
            for item in result.assessments
            if item.criterion_id.startswith("observation:source-access")
        )
        self.assertEqual(source.status, CriterionStatus.PENDING)

    def test_source_access_evidence_is_not_duplicated_across_repeated_checks(self) -> None:
        ledger = SessionLedger("policy-source-dedupe")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Use https://public.example.com/data to write outputs/report.md.",
            public_schema=None,
        )
        ledger.append(
            "tool/action-audited",
            {
                "record": {
                    "intent": "read",
                    "resources": ["https://public.example.com/data"],
                    "scope_key": "source",
                    "argument_fingerprint": "source",
                    "mutation_epoch": 0,
                },
                "decision": {"disposition": "allow"},
            },
            turn=1,
        )

        session.check_resources(ledger, progress=None, turn=1)
        session.check_resources(ledger, progress=None, turn=1)
        state = TaskStateProjector().project(ledger.events)

        self.assertEqual(
            sum(1 for item in state.evidence if item.subject.startswith("source.access")),
            1,
        )


    def test_source_access_for_one_url_does_not_satisfy_another_url(self) -> None:
        ledger = SessionLedger("policy-source-partial")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt=(
                "Open https://public.example.com/data and read "
                "https://public.example.com/help before writing outputs/report.md."
            ),
            public_schema=None,
        )
        ledger.append(
            "tool/action-audited",
            {
                "record": {
                    "intent": "read",
                    "resources": ["https://public.example.com/data"],
                    "scope_key": "source",
                    "argument_fingerprint": "source",
                    "mutation_epoch": 0,
                },
                "decision": {"disposition": "allow"},
            },
            turn=1,
        )

        session.check_resources(ledger, progress=None, turn=1)
        result, _feedback, _recovery = session.check_completion(ledger)

        source_assessments = [
            item
            for item in result.assessments
            if item.criterion_id.startswith("observation:source-access")
        ]
        self.assertEqual(
            [item.status for item in source_assessments].count(CriterionStatus.SATISFIED),
            1,
        )
        self.assertEqual(
            [item.status for item in source_assessments].count(CriterionStatus.PENDING),
            1,
        )

    def test_source_access_evidence_is_not_duplicated_across_turns(self) -> None:
        ledger = SessionLedger("policy-source-turn-dedupe")
        session = KernelPolicySession(completion_gate=EvidenceCompletionGate())
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Access https://public.example.com/data before writing outputs/report.md.",
            public_schema=None,
        )
        for turn in (1, 2):
            ledger.append(
                "tool/action-audited",
                {
                    "record": {
                        "intent": "read",
                        "resources": ["https://public.example.com/data"],
                        "scope_key": f"source-{turn}",
                        "argument_fingerprint": f"source-{turn}",
                        "mutation_epoch": 0,
                    },
                    "decision": {"disposition": "allow"},
                },
                turn=turn,
            )
            session.check_resources(ledger, progress=None, turn=turn)

        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(sum(1 for item in state.evidence if item.subject.startswith("source.access")), 1)

    def test_context_retrieves_promoted_experience_without_model_visible_provenance(self) -> None:
        store = ExperienceStore(SessionLedger("policy-experience-store"))
        experience = Experience(
            "timeout-recovery",
            1,
            "tool_recovery",
            "TIMEOUT",
            "cli",
            "A command timed out before producing evidence.",
            "Narrow the command and observe fresh public state.",
            "Do not repeat the unchanged command.",
            "A new task fact is recorded.",
            "Stop after another no-progress result.",
            ("dev-a", "dev-b", "dev-c"),
        )
        store.create_candidate(experience)
        store.start_shadow(experience.experience_id, 1, reason="admission passed")
        store.promote(
            experience.experience_id,
            1,
            TransferValidation(
                ("transfer-a", "transfer-b"),
                0,
                False,
                "evidence://transfer",
                effective_task_ids=("transfer-a",),
            ),
        )
        session = KernelPolicySession(
            context_manager=TaskAwareContextManager(
                budget=ContextBudget(max_input_tokens=800),
                experience_retriever=TaskStateExperienceRetriever(store),
            )
        )

        prepared = session.prepare_context(
            ({"role": "user", "content": "Complete the task."},),
            environment_state={},
            task_state={
                "task_state": "tool_recovery",
                "failure_type": "TIMEOUT",
                "runtime_surface": "cli",
            },
        )

        rendered = str(prepared.messages)
        self.assertIn("Narrow the command", rendered)
        self.assertNotIn("timeout-recovery", rendered)
        self.assertNotIn("dev-a", rendered)

    def test_prepare_context_reuses_experience_retrieval_semantics(self) -> None:
        store = ExperienceStore(SessionLedger("policy-session-experience-store"))
        experience = Experience(
            experience_id="recover-timeout",
            version=1,
            task_state="tool_recovery",
            failure_type="TIMEOUT",
            runtime_surface="cli",
            situation="A command timed out before producing task evidence.",
            strategy="Use a narrower command and inspect fresh evidence.",
            anti_pattern="Do not repeat the unchanged long-running command.",
            progress_signal="The narrower command produces new task evidence.",
            stop_condition="Stop after another no-progress outcome.",
            source_task_ids=("dev-a", "dev-b", "dev-c"),
        )
        store.create_candidate(experience)
        store.start_shadow(experience.experience_id, experience.version, reason="offline checks passed")
        store.promote(
            experience.experience_id,
            experience.version,
            TransferValidation(
                ("transfer-a", "transfer-b"),
                stable_pass_regressions=0,
                harm_observed=False,
                evidence_ref="evidence://transfer",
                effective_task_ids=("transfer-a",),
            ),
        )

        session = KernelPolicySession(
            context_manager=TaskAwareContextManager(
                budget=ContextBudget(max_input_tokens=900),
                experience_retriever=TaskStateExperienceRetriever(store),
            )
        )
        prepared = session.prepare_context(
            ({"role": "user", "content": "Complete the task."},),
            environment_state={},
            task_state={
                "task": {
                    "task_state": "tool_recovery",
                    "failure_type": "TIMEOUT",
                    "runtime_surface": "cli",
                }
            },
        )

        self.assertEqual(prepared.audit["rejected_experiences"], 0)
        self.assertIn("structured task state", str(prepared.messages))
        self.assertIn("Use a narrower command", str(prepared.messages))

    def test_bound_task_state_is_frozen_without_mutable_aliases(self) -> None:
        session = KernelPolicySession()
        source = {
            "task": {
                "failures": [{"error_type": "TIMEOUT", "message": "before"}],
                "latest_completion": {"passed": False},
            }
        }

        with bind_policy_session(session, task_state=source):
            source["task"]["failures"][0]["message"] = "after"
            snapshot = current_policy_task_state()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["task"]["failures"][0]["message"], "before")
        with self.assertRaises(TypeError):
            snapshot["task"] = {}  # type: ignore[index]


    def test_resource_guardrail_only_processes_new_action_audits_once(self) -> None:
        session = KernelPolicySession(resource_guardrail=ResourceGuardrail())
        ledger = SessionLedger("policy-session-guardrail")
        for seq, scope in enumerate(("same", "same", "same"), start=1):
            ledger.append(
                "tool/action-audited",
                {
                    "record": {
                        "intent": "read",
                        "scope_key": scope,
                        "argument_fingerprint": "same-fingerprint",
                        "mutation_epoch": 0,
                    },
                    "decision": {"disposition": "allow"},
                },
                turn=1,
            )

        first = session.check_resources(ledger, progress=None, turn=1)
        second = session.check_resources(ledger, progress=None, turn=1)

        self.assertEqual(
            [item["disposition"] for item in first],
            ["record", "replan", "block_scope"],
        )
        self.assertEqual(second, ())


if __name__ == "__main__":
    unittest.main()
