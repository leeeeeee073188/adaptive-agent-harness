from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.action_audit import emit_action_audit
from adaptive_harness.capabilities import (
    AcceptFinalCompletion,
    ModelResponse,
    PassthroughContextManager,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from adaptive_harness.integrations.deerflow import (
    CanonicalDeerFlowRuntimeAdapter,
    DeerFlowRuntimeAdapter,
)
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    FileArtifactObservationProvider,
)
from adaptive_harness.kernel import Kernel, PluginContext
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.progress import RuleBasedProgressDetector
from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.runtime import AgentDriver, AgentDriverRuntimeAdapter
from adaptive_harness.runtime_contract import RuntimeRequest
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    ENVIRONMENT,
    MODEL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
    TASK_RECOVERY_EXECUTOR,
    TASK_RECOVERY_POLICY,
    TOOL_RUNTIME,
)
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder
from adaptive_harness.task_state import EvidenceCompletionGate
from adaptive_harness.tool_runtime import ToolRuntime


class RecordingEnvironment:
    def __init__(self) -> None:
        self.built = False
        self.cleaned = False

    async def build(self) -> None:
        self.built = True

    async def cleanup(self) -> None:
        self.cleaned = True
        self.built = False

    def state(self):
        return {"ready": self.built}

    async def tools(self):
        return ()


class FinalModel:
    async def complete(self, request):
        return ModelResponse(content="done", usage={"total_tokens": 3})


class FailingModel:
    async def complete(self, request):
        raise RuntimeError("model failed")


class RepeatedReadModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls <= 3:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        f"read-{self.calls}",
                        "read_file",
                        {"path": "/task/workspace/input.json"},
                    ),
                )
            )
        return ModelResponse(content="done")


class ArtifactEvidenceModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(ToolCall("inspect", "inspect_artifact", {}),)
            )
        return ModelResponse(content="done")


class RuntimePlugin:
    name = "runtime"
    requires = ()

    def __init__(self, environment: RecordingEnvironment, model=FinalModel()) -> None:
        self.environment = environment
        self.model = model

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, self.environment)
        context.provide(MODEL, self.model)
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(TOOL_RUNTIME, ToolRuntime(()))
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())


class RepeatedActionPlugin(RuntimePlugin):
    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, self.environment)
        context.provide(MODEL, self.model)
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(
            TOOL_RUNTIME,
            ToolRuntime(
                (
                    ToolDefinition(
                        "read_file",
                        "read one file",
                        lambda path: "unchanged",
                    ),
                )
            ),
        )
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())
        context.provide(TASK_RECOVERY_POLICY, RuleBasedTaskRecoveryPolicy())
        context.provide(TASK_RECOVERY_EXECUTOR, RuleBasedTaskRecoveryExecutor())


class FormalEvidencePlugin(RuntimePlugin):
    def __init__(self, environment: RecordingEnvironment, *, with_evidence: bool) -> None:
        super().__init__(
            environment,
            ArtifactEvidenceModel() if with_evidence else FinalModel(),
        )
        self.with_evidence = with_evidence

    async def mount(self, context: PluginContext) -> None:
        context.provide(ENVIRONMENT, self.environment)
        context.provide(MODEL, self.model)
        context.provide(CONTEXT_MANAGER, PassthroughContextManager())
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())
        context.provide(TASK_CONTRACT_BUILDER, RuleBasedTaskContractBuilder())
        context.provide(TASK_COMPLETION_GATE, EvidenceCompletionGate())

        def inspect_artifact() -> ToolResult:
            return ToolResult(
                "inspect",
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
            ToolRuntime(
                (ToolDefinition("inspect_artifact", "inspect artifact", inspect_artifact),)
                if self.with_evidence
                else ()
            ),
        )


class FakeDeerFlowClient:
    def stream(self, message, *, thread_id=None, **kwargs):
        yield {
            "type": "values",
            "data": {
                "messages": [
                    {"type": "human", "id": "u1", "content": message},
                    {"type": "ai", "id": "a1", "content": "done"},
                ]
            },
        }
        yield {"type": "end", "data": {"usage": {"total_tokens": 3}}}


class FailingDeerFlowClient:
    def stream(self, message, *, thread_id=None, **kwargs):
        raise RuntimeError("stream failed")
        yield  # pragma: no cover


class AuditedDeerFlowClient:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, message, *, thread_id=None, **kwargs):
        self.calls += 1
        emit_action_audit(
            {
                "policy": "tool-action-ledger-v1",
                "mode": "observe",
                "record": {
                    "scope_key": "same-scope",
                    "argument_fingerprint": "same-strategy",
                    "mutation_epoch": 0,
                    "intent": "read",
                },
                "decision": {"disposition": "allow"},
                "advice_applied": False,
            }
        )
        yield {
            "type": "values",
            "data": {"messages": [{"type": "ai", "id": f"a{self.calls}", "content": "done"}]},
        }
        yield {"type": "end", "data": {"usage": {"total_tokens": 1}}}


class RuntimeConformanceTests(unittest.IsolatedAsyncioTestCase):
    async def _agent_adapter(self, *, failing: bool = False):
        environment = RecordingEnvironment()
        kernel = Kernel()
        await kernel.mount(
            RuntimePlugin(environment, FailingModel() if failing else FinalModel())
        )
        return AgentDriverRuntimeAdapter(
            AgentDriver(kernel, allow_unverified_completion=True)
        ), environment

    async def _deerflow_adapter(self, *, failing: bool = False):
        environment = RecordingEnvironment()
        runtime = DeerFlowRuntimeAdapter(
            FailingDeerFlowClient() if failing else FakeDeerFlowClient(),
            environment,
            policy_bridge=DeerFlowPolicyBridge(
                unsupported_criteria="observe_only",
                allow_unverified_completion=True,
            ),
        )
        return CanonicalDeerFlowRuntimeAdapter(runtime), environment

    async def test_adapters_share_canonical_lifecycle_and_contract_order(self) -> None:
        for name, factory in (
            ("agent", self._agent_adapter),
            ("deerflow", self._deerflow_adapter),
        ):
            with self.subTest(adapter=name):
                adapter, environment = await factory()
                result = await adapter.run(
                    RuntimeRequest(
                        "Do the task.",
                        run_id=f"run-{name}",
                        task_id="public-task",
                    )
                )
                event_types = [event.type for event in result.ledger.events]

                self.assertTrue(result.completed)
                self.assertTrue(environment.cleaned)
                self.assertEqual(event_types[0], "runtime/start")
                self.assertIn("task/contract-created", event_types)
                self.assertIn("request/header", event_types)
                self.assertIn("assistant/message", event_types)
                self.assertIn("completion/checked", event_types)
                self.assertEqual(event_types[-1], "runtime/end")
                self.assertLess(
                    event_types.index("task/contract-created"),
                    event_types.index("request/header"),
                )

    async def test_adapters_persist_error_and_cleanup_on_failure(self) -> None:
        for name, factory in (
            ("agent", self._agent_adapter),
            ("deerflow", self._deerflow_adapter),
        ):
            with self.subTest(adapter=name), tempfile.TemporaryDirectory() as tmp:
                adapter, environment = await factory(failing=True)
                ledger_path = Path(tmp) / "ledger.jsonl"

                with self.assertRaisesRegex(RuntimeError, "failed"):
                    await adapter.run(
                        RuntimeRequest(
                            "Do the task.",
                            run_id=f"failed-{name}",
                            task_id="public-task",
                            ledger_path=ledger_path,
                        )
                    )

                replayed = SessionLedger.replay(ledger_path)
                self.assertTrue(environment.cleaned)
                self.assertIn("runtime/error", [event.type for event in replayed.events])

    async def test_secret_options_fail_before_environment_build(self) -> None:
        for name, factory in (
            ("agent", self._agent_adapter),
            ("deerflow", self._deerflow_adapter),
        ):
            with self.subTest(adapter=name):
                adapter, environment = await factory()

                with self.assertRaisesRegex(ValueError, "credential"):
                    await adapter.run(
                        RuntimeRequest(
                            "Do the task.",
                            run_id=f"secret-{name}",
                            client_options={"primary_api_key": "not-recordable"},
                        )
                    )

                self.assertFalse(environment.built)

    async def test_safe_token_budget_options_are_not_misclassified_as_credentials(self) -> None:
        adapter, environment = await self._agent_adapter()

        result = await adapter.run(
            RuntimeRequest(
                "Do the task.",
                run_id="safe-token-budget",
                client_options={"max_tokens": 4096, "token_budget": 600000},
            )
        )

        self.assertTrue(result.completed)
        self.assertTrue(environment.cleaned)

    async def test_default_adapters_require_and_accept_provider_backed_evidence(self) -> None:
        for with_evidence in (False, True):
            with self.subTest(with_evidence=with_evidence), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if with_evidence:
                    output = root / "outputs/report.csv"
                    output.parent.mkdir()
                    output.write_text("ok", encoding="utf-8")

                agent_environment = RecordingEnvironment()
                agent_kernel = Kernel()
                await agent_kernel.mount(
                    FormalEvidencePlugin(
                        agent_environment,
                        with_evidence=with_evidence,
                    )
                )
                agent = AgentDriverRuntimeAdapter(
                    AgentDriver(agent_kernel, max_steps=2)
                )

                deerflow_environment = RecordingEnvironment()
                deerflow = CanonicalDeerFlowRuntimeAdapter(
                    DeerFlowRuntimeAdapter(
                        FakeDeerFlowClient(),
                        deerflow_environment,
                        policy_bridge=DeerFlowPolicyBridge(
                            observation_providers=(
                                FileArtifactObservationProvider(root),
                            ),
                            max_completion_turns=1,
                        ),
                    )
                )

                agent_result = await agent.run(
                    RuntimeRequest(
                        "Write outputs/report.csv.",
                        run_id=f"formal-agent-{with_evidence}",
                        task_id="public-task",
                    )
                )
                deerflow_result = await deerflow.run(
                    RuntimeRequest(
                        "Write outputs/report.csv.",
                        run_id=f"formal-deerflow-{with_evidence}",
                        task_id="public-task",
                    )
                )

                self.assertEqual(agent_result.completed, with_evidence)
                self.assertEqual(deerflow_result.completed, with_evidence)
                for result in (agent_result, deerflow_result):
                    checks = [
                        event
                        for event in result.ledger.events
                        if event.type == "completion/checked"
                    ]
                    self.assertEqual(checks[-1].payload["passed"], with_evidence)

    async def test_adapters_share_no_progress_resource_semantics(self) -> None:
        agent_environment = RecordingEnvironment()
        kernel = Kernel()
        await kernel.mount(RepeatedActionPlugin(agent_environment, RepeatedReadModel()))
        agent = AgentDriverRuntimeAdapter(
            AgentDriver(
                kernel,
                max_steps=5,
                allow_unverified_completion=True,
            )
        )

        deerflow_environment = RecordingEnvironment()
        deerflow = CanonicalDeerFlowRuntimeAdapter(
            DeerFlowRuntimeAdapter(
                AuditedDeerFlowClient(),
                deerflow_environment,
                policy_bridge=DeerFlowPolicyBridge(
                    max_completion_turns=3,
                    unsupported_criteria="reject",
                    progress_detector=RuleBasedProgressDetector(),
                    resource_guardrail=ResourceGuardrail(),
                ),
            )
        )

        agent_result = await agent.run(
            RuntimeRequest("Do the task.", run_id="agent-no-progress")
        )
        deerflow_result = await deerflow.run(
            RuntimeRequest(
                "Write outputs/report.csv.",
                run_id="deerflow-no-progress",
                task_id="public-task",
            )
        )

        for result in (agent_result, deerflow_result):
            resource = [
                event.payload["disposition"]
                for event in result.ledger.events
                if event.type == "resource/no-progress-checked"
            ]
            self.assertEqual(resource[:3], ["record", "replan", "block_scope"])


if __name__ == "__main__":
    unittest.main()
