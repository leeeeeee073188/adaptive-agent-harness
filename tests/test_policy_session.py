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
from adaptive_harness.policy_session import KernelPolicySession
from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
    TaskRecoveryAction,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.task_state import EvidenceCompletionGate, TaskStateProjector


class PolicySessionTests(unittest.TestCase):
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
