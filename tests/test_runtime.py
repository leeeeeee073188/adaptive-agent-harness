from __future__ import annotations

import unittest

from adaptive_harness.capabilities import (
    AcceptFinalCompletion,
    ModelResponse,
    PassthroughContextManager,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from adaptive_harness.context import ContextBudget, TaskAwareContextManager
from adaptive_harness.kernel import Kernel, PluginContext
from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.runtime import AgentDriver
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    ENVIRONMENT,
    MODEL,
    RESOURCE_GUARDRAIL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
    TASK_RECOVERY_EXECUTOR,
    TASK_RECOVERY_POLICY,
    TOOL_RUNTIME,
)
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder
from adaptive_harness.task_state import EvidenceCompletionGate
from adaptive_harness.tool_reliability import ToolReliabilityConfig
from adaptive_harness.tool_runtime import ToolRuntime


class FakeEnvironment:
    def __init__(self) -> None:
        self.built = False

    async def build(self) -> None:
        self.built = True

    async def cleanup(self) -> None:
        self.built = False

    def state(self):
        return {"ready": self.built}

    async def tools(self):
        return []


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(tool_calls=(ToolCall("c1", "echo", {"value": "ok"}),))
        return ModelResponse(content="finished", usage={"total_tokens": 5})


class RuntimePlugin:
    name = "runtime"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, FakeModel())
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(
            TOOL_RUNTIME,
            ToolRuntime([ToolDefinition("echo", "echo a value", lambda value: value)]),
        )


class ContractPlugin:
    name = "task-contract"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())


class PrematureModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(content="done")
        if self.calls == 2:
            return ModelResponse(tool_calls=(ToolCall("inspect-call", "inspect_artifact", {}),))
        return ModelResponse(content="done")


class CompletionRuntimePlugin:
    name = "completion-runtime"
    requires = ()

    def __init__(self, *, gate_enabled: bool) -> None:
        self.gate_enabled = gate_enabled

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, PrematureModel())
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())
        if self.gate_enabled:
            context.provide(TASK_COMPLETION_GATE, EvidenceCompletionGate())

        def inspect_artifact() -> ToolResult:
            return ToolResult(
                "provider-call",
                "artifact exists",
                metadata={
                    "evidence": [
                        {
                            "kind": "artifact",
                            "subject": "outputs/report.csv",
                            "value": {"exists": True},
                        }
                    ]
                },
            )

        context.provide(
            TOOL_RUNTIME,
            ToolRuntime([ToolDefinition("inspect_artifact", "inspect output artifact", inspect_artifact)]),
        )


class ReliabilityRuntimePlugin:
    name = "reliability-runtime"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        calls = 0

        def flaky() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("temporary timeout")
            return "recovered"

        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, FakeModel())
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(
            TOOL_RUNTIME,
            ToolRuntime(
                [ToolDefinition("echo", "recoverable operation", lambda value: flaky())],
                reliability=ToolReliabilityConfig(enabled=True, max_attempts=2),
            ),
        )


class GuardrailModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls <= 4:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        f"inspect-{self.calls}",
                        "inspect_artifact",
                        {"path": "outputs/report.csv"},
                    ),
                )
            )
        return ModelResponse(content="finished", usage={"total_tokens": 8})


class GuardrailRuntimePlugin:
    name = "guardrail-runtime"
    requires = ()

    def __init__(self) -> None:
        self.executions = 0

    def _inspect(self, path: str) -> str:
        self.executions += 1
        return "no change"

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, GuardrailModel())
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(RESOURCE_GUARDRAIL, ResourceGuardrail())
        context.provide(
            TOOL_RUNTIME,
            ToolRuntime(
                [ToolDefinition("inspect_artifact", "inspect output artifact", self._inspect)],
            ),
        )


class DeliveryRecoveryModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(content="done too early")
        if self.calls == 2:
            return ModelResponse(
                tool_calls=(ToolCall("read-after-recovery", "read_file", {}),)
            )
        if self.calls == 3:
            return ModelResponse(
                tool_calls=(ToolCall("write-after-recovery", "write_artifact", {}),)
            )
        return ModelResponse(content="done with artifact")


class DeliveryRecoveryPlugin:
    name = "delivery-recovery-runtime"
    requires = ()

    def __init__(self) -> None:
        self.read_executions = 0
        self.write_executions = 0

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, DeliveryRecoveryModel())
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())
        context.provide(TASK_COMPLETION_GATE, EvidenceCompletionGate())
        context.provide(TASK_RECOVERY_POLICY, RuleBasedTaskRecoveryPolicy())
        context.provide(TASK_RECOVERY_EXECUTOR, RuleBasedTaskRecoveryExecutor())

        def read_file() -> str:
            self.read_executions += 1
            return "should not execute"

        def write_artifact() -> ToolResult:
            self.write_executions += 1
            return ToolResult(
                "write-after-recovery",
                "written",
                metadata={
                    "evidence": [
                        {
                            "kind": "artifact",
                            "subject": "outputs/report.csv",
                            "value": {"exists": True},
                        }
                    ]
                },
            )

        context.provide(
            TOOL_RUNTIME,
            ToolRuntime(
                (
                    ToolDefinition("read_file", "read", read_file),
                    ToolDefinition("write_artifact", "write", write_artifact),
                )
            ),
        )


class TaskAwareRuntimePlugin:
    name = "task-aware-runtime"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, FakeEnvironment())
        context.provide(MODEL, FakeModel())
        context.provide(
            CONTEXT_MANAGER,
            TaskAwareContextManager(budget=ContextBudget(max_input_tokens=256)),
        )
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(
            TOOL_RUNTIME,
            ToolRuntime([ToolDefinition("echo", "echo a value", lambda value: value)]),
        )


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_blocks_reads_until_required_artifact_write(self) -> None:
        kernel = Kernel()
        plugin = DeliveryRecoveryPlugin()
        await kernel.mount(plugin)

        result = await AgentDriver(kernel, max_steps=5).run(
            "Write outputs/report.csv.",
            run_id="run-delivery-recovery",
        )

        self.assertTrue(result.completed)
        self.assertEqual(plugin.read_executions, 0)
        self.assertEqual(plugin.write_executions, 1)
        self.assertEqual(
            sum(
                event.type == "resource/delivery-first-blocked"
                for event in result.ledger.events
            ),
            1,
        )

    async def test_task_aware_context_selection_is_audited_before_each_request(self) -> None:
        kernel = Kernel()
        await kernel.mount(TaskAwareRuntimePlugin())

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run("do the task", run_id="run-context")

        events = result.ledger.events
        selections = [event for event in events if event.type == "context/selected"]
        headers = [event for event in events if event.type == "request/header"]
        self.assertEqual(len(selections), len(headers))
        self.assertEqual(len(selections), 2)
        for selection in selections:
            self.assertLessEqual(selection.payload["estimated_input_tokens"], 256)
            self.assertEqual(len(selection.payload["surface_sha256"]), 64)
            self.assertNotIn("do the task", str(selection.payload))
            header_index = next(
                index
                for index, event in enumerate(events)
                if event.type == "request/header" and event.step == selection.step
            )
            self.assertLess(events.index(selection), header_index)

    async def test_driver_records_reconstructable_tool_turn(self) -> None:
        kernel = Kernel()
        await kernel.mount(RuntimePlugin())

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run("do the task", run_id="run-1")

        self.assertTrue(result.completed)
        self.assertEqual(result.content, "finished")
        self.assertEqual(result.steps, 2)
        self.assertEqual(
            [event.type for event in result.ledger.events],
            [
                "runtime/start",
                "task/contract-created",
                "policy/configured",
                "turn/start",
                "user/message",
                "step/start",
                "phase/evaluated",
                "state/updated",
                "request/header",
                "assistant/message",
                "tool/call",
                "tool/result",
                "tool/action-audited",
                "progress/checked",
                "state/updated",
                "resource/no-progress-checked",
                "state/updated",
                "step/end",
                "step/start",
                "request/header",
                "assistant/message",
                "progress/checked",
                "state/updated",
                "completion/checked",
                "phase/evaluated",
                "state/updated",
                "step/end",
                "turn/end",
                "runtime/end",
            ],
        )
        self.assertIn("ok", str(result.ledger.derive_messages()))
        headers = [event for event in result.ledger.events if event.type == "request/header"]
        self.assertEqual(headers[0].payload["context"]["step"], 1)
        self.assertIn("Current runtime snapshot", str(headers[0].payload["messages"]))

    async def test_driver_projects_public_contract_into_recorded_request(self) -> None:
        kernel = Kernel()
        await kernel.mount(ContractPlugin())
        await kernel.mount(RuntimePlugin())

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run(
            "Write outputs/report.csv.",
            run_id="run-contract",
            task_id="public-task",
        )

        contract_event = next(event for event in result.ledger.events if event.type == "task/contract-created")
        contract = contract_event.payload["contract"]
        self.assertEqual(contract["task_id"], "public-task")
        self.assertEqual(contract["criteria"][0]["parameters"]["path"], "outputs/report.csv")
        first_header = next(event for event in result.ledger.events if event.type == "request/header")
        self.assertIn("artifact_exists", str(first_header.payload["messages"]))

    async def test_contract_rejection_still_cleans_environment(self) -> None:
        kernel = Kernel()
        contract_plugin = ContractPlugin()
        runtime_plugin = RuntimePlugin()
        await kernel.mount(contract_plugin)
        await kernel.mount(runtime_plugin)
        environment = kernel.services.get(ENVIRONMENT)

        with self.assertRaisesRegex(ValueError, "evaluation-only field"):
            await AgentDriver(kernel).run(
                "Do the task.",
                run_id="run-rejected",
                public_schema={"ground_truth": "forbidden"},
            )

        self.assertFalse(environment.built)

    async def test_completion_gate_rejects_claim_until_artifact_evidence_exists(self) -> None:
        kernel = Kernel()
        await kernel.mount(CompletionRuntimePlugin(gate_enabled=True))

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run(
            "Write outputs/report.csv.",
            run_id="run-gated",
        )

        checks = [event for event in result.ledger.events if event.type == "completion/checked"]
        self.assertTrue(result.completed)
        self.assertEqual(result.steps, 3)
        self.assertFalse(checks[0].payload["passed"])
        self.assertTrue(checks[-1].payload["passed"])
        self.assertIn("Missing artifact evidence", checks[0].payload["missing"][0])
        self.assertEqual(sum(event.type == "evidence/added" for event in result.ledger.events), 1)
        self.assertIn("Completion rejected by task evidence", str(result.ledger.derive_messages()))

    async def test_completion_gate_can_be_disabled_for_ablation(self) -> None:
        kernel = Kernel()
        await kernel.mount(CompletionRuntimePlugin(gate_enabled=False))

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run(
            "Write outputs/report.csv.",
            run_id="run-ungated",
        )

        self.assertTrue(result.completed)
        self.assertEqual(result.steps, 1)
        self.assertEqual(sum(event.type == "evidence/added" for event in result.ledger.events), 0)

    async def test_driver_records_classified_failure_and_bounded_recovery(self) -> None:
        kernel = Kernel()
        await kernel.mount(ReliabilityRuntimePlugin())

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run("Do the task.", run_id="run-retry")

        failure = next(event for event in result.ledger.events if event.type == "failure/classified")
        tool_result = next(event for event in result.ledger.events if event.type == "tool/result")
        self.assertTrue(result.completed)
        self.assertEqual(failure.payload["failure"]["error_type"], "TIMEOUT")
        self.assertEqual(failure.payload["failure"]["metadata"]["recovery"], "retry")
        self.assertEqual(tool_result.payload["metadata"]["attempts"], 2)
        self.assertLess(failure.seq, tool_result.seq)

    async def test_driver_escalates_repeated_no_progress_scopes(self) -> None:
        kernel = Kernel()
        plugin = GuardrailRuntimePlugin()
        await kernel.mount(plugin)

        result = await AgentDriver(
            kernel,
            allow_unverified_completion=True,
        ).run("Inspect the artifact.", run_id="run-guardrail")

        guardrail = [event for event in result.ledger.events if event.type == "resource/no-progress-checked"]
        action_audits = [event for event in result.ledger.events if event.type == "tool/action-audited"]
        self.assertTrue(result.completed)
        self.assertEqual(
            [event.payload["disposition"] for event in guardrail[:3]],
            ["record", "replan", "block_scope"],
        )
        self.assertEqual(len(action_audits), 4)
        self.assertEqual(plugin.executions, 3)
        self.assertEqual(
            sum(event.type == "resource/action-blocked" for event in result.ledger.events),
            1,
        )
