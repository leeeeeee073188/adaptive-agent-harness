from __future__ import annotations

import unittest

from adaptive_harness.capabilities import AcceptFinalCompletion, ModelResponse
from adaptive_harness.config import Bundle, PluginSpec, Profile
from adaptive_harness.context import ContextBudget, TaskAwareContextManager
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.policy_session import PolicySession
from adaptive_harness.profiles import (
    assemble_policy_kernel,
    candidate_policy_profile,
    policy_session_from_kernel,
)
from adaptive_harness.progress import RuleBasedProgressDetector
from adaptive_harness.recovery import (
    RuleBasedRecoveryOutcomeEvaluator,
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    PROGRESS_DETECTOR,
    RECOVERY_OUTCOME_EVALUATOR,
    RESOURCE_GUARDRAIL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
    TASK_RECOVERY_EXECUTOR,
    TASK_RECOVERY_POLICY,
)
from adaptive_harness.task_contract import ContractBuilder, TaskContract
from adaptive_harness.task_state import EvidenceCompletionGate


class CustomContractBuilder:
    def build(self, task_id, task_prompt, public_schema=None):
        return TaskContract(task_id, task_prompt, (), None)


class PolicyProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidate_policy_profile_mounts_executable_policy_services(self) -> None:
        profile = candidate_policy_profile()
        kernel = await assemble_policy_kernel(profile)
        try:
            self.assertEqual(
                kernel.mounted_plugins,
                (
                    "context",
                    "response_completion",
                    "task_contract_builder",
                    "evidence_completion",
                    "semantic_progress",
                    "durable_recovery",
                    "resource_guardrail",
                ),
            )
            self.assertIsInstance(kernel.services.get(CONTEXT_MANAGER), TaskAwareContextManager)
            self.assertIsInstance(
                kernel.services.get(COMPLETION_POLICY),
                AcceptFinalCompletion,
            )
            self.assertIsInstance(kernel.services.get(TASK_COMPLETION_GATE), EvidenceCompletionGate)
            self.assertIsInstance(kernel.services.get(PROGRESS_DETECTOR), RuleBasedProgressDetector)
            self.assertIsInstance(kernel.services.get(TASK_RECOVERY_POLICY), RuleBasedTaskRecoveryPolicy)
            self.assertIsInstance(kernel.services.get(TASK_RECOVERY_EXECUTOR), RuleBasedTaskRecoveryExecutor)
            self.assertIsInstance(
                kernel.services.get(RECOVERY_OUTCOME_EVALUATOR),
                RuleBasedRecoveryOutcomeEvaluator,
            )
            self.assertIsInstance(kernel.services.get(RESOURCE_GUARDRAIL), ResourceGuardrail)
        finally:
            await kernel.close()

    async def test_same_profile_fingerprint_reproduces_same_mounts_and_service_keys(self) -> None:
        left = candidate_policy_profile()
        right = candidate_policy_profile()

        self.assertEqual(left.fingerprint(), right.fingerprint())

        left_kernel = await assemble_policy_kernel(left)
        right_kernel = await assemble_policy_kernel(right)
        try:
            self.assertEqual(left_kernel.mounted_plugins, right_kernel.mounted_plugins)
            self.assertEqual(left_kernel.services.snapshot(), right_kernel.services.snapshot())
        finally:
            await left_kernel.close()
            await right_kernel.close()

    async def test_contract_builder_can_be_overridden_without_changing_profile(self) -> None:
        custom: ContractBuilder = CustomContractBuilder()
        profile = candidate_policy_profile()

        kernel = await assemble_policy_kernel(profile, contract_builder=custom)
        try:
            self.assertIs(kernel.services.get(TASK_CONTRACT_BUILDER), custom)
        finally:
            await kernel.close()

    async def test_experience_retriever_is_wired_into_context_manager(self) -> None:
        class Retriever:
            def retrieve(self, task_state):
                return ()

        retriever = Retriever()
        kernel = await assemble_policy_kernel(
            candidate_policy_profile(),
            experience_retriever=retriever,
        )
        try:
            context_manager = kernel.services.get(CONTEXT_MANAGER)
            self.assertIsInstance(context_manager, TaskAwareContextManager)
            self.assertIs(context_manager.experience_retriever, retriever)
        finally:
            await kernel.close()

    async def test_context_config_overrides_budget_without_runtime_objects_in_fingerprint(self) -> None:
        profile = candidate_policy_profile(context_config={"max_input_tokens": 512})

        self.assertEqual(profile.fingerprint(), profile.fingerprint())
        kernel = await assemble_policy_kernel(profile)
        try:
            context_manager = kernel.services.get(CONTEXT_MANAGER)
            self.assertIsInstance(context_manager, TaskAwareContextManager)
            self.assertEqual(context_manager.budget.max_input_tokens, 512)
            self.assertEqual(
                context_manager.budget.recent_history_fraction,
                ContextBudget.recent_history_fraction,
            )
        finally:
            await kernel.close()

    async def test_policy_session_from_kernel_uses_assembled_services_and_is_fresh(self) -> None:
        custom: ContractBuilder = CustomContractBuilder()
        kernel = await assemble_policy_kernel(candidate_policy_profile(), contract_builder=custom)
        try:
            first = policy_session_from_kernel(kernel)
            second = policy_session_from_kernel(kernel)

            self.assertIsInstance(first, PolicySession)
            self.assertIsInstance(second, PolicySession)
            self.assertIsNot(first, second)
            self.assertIs(first.contract_builder, custom)
            self.assertIs(first.context_manager, kernel.services.get(CONTEXT_MANAGER))
            self.assertIs(first.completion_gate, kernel.services.get(TASK_COMPLETION_GATE))
            self.assertEqual(first.unsupported_criteria, "observe_only")
        finally:
            await kernel.close()

    async def test_formal_profile_fails_closed_without_provider_backed_criteria(self) -> None:
        kernel = await assemble_policy_kernel(candidate_policy_profile())
        try:
            session = policy_session_from_kernel(kernel)
            ledger = SessionLedger("formal-no-evidence")
            session.start_contract(
                ledger,
                task_id="public-task",
                task_prompt="Complete the task.",
                public_schema=None,
            )

            result, feedback, _ = session.check_completion(
                ledger,
                response=ModelResponse(content="done"),
            )

            self.assertFalse(result.passed)
            self.assertIn("provider-backed criteria", feedback or "")
        finally:
            await kernel.close()

    def test_policy_session_from_incomplete_kernel_fails_closed(self) -> None:
        from adaptive_harness.kernel import Kernel

        with self.assertRaisesRegex(KeyError, "context_manager"):
            policy_session_from_kernel(Kernel())

    async def test_unknown_policy_plugin_fails_closed(self) -> None:
        profile = Profile(
            "unsafe",
            (Bundle("base", (PluginSpec("context"), PluginSpec("unknown_policy"))),),
        )

        with self.assertRaisesRegex(KeyError, "unknown_policy"):
            await assemble_policy_kernel(profile)

    async def test_unknown_plugin_config_fails_closed(self) -> None:
        profile = candidate_policy_profile(
            overlays=(PluginSpec("context", {"unknown": True}),),
        )

        with self.assertRaisesRegex(ValueError, "unknown config"):
            await assemble_policy_kernel(profile)

    def test_core_policy_profile_module_has_no_benchmark_or_deerflow_imports(self) -> None:
        import adaptive_harness.profiles as profiles

        source = profiles.__loader__.get_source(profiles.__name__)
        assert source is not None
        self.assertNotIn("adaptive_harness.integrations", source)
        self.assertNotIn("deerflow", source.lower())
        self.assertNotIn("realreplica", source.lower())


if __name__ == "__main__":
    unittest.main()
