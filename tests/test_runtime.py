from __future__ import annotations

import unittest

from adaptive_harness.capabilities import (
    AcceptFinalCompletion,
    ModelResponse,
    PassthroughContextManager,
    ToolCall,
    ToolDefinition,
)
from adaptive_harness.kernel import Kernel, PluginContext
from adaptive_harness.runtime import AgentDriver
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    ENVIRONMENT,
    MODEL,
    TASK_CONTRACT_BUILDER,
    TOOL_RUNTIME,
)
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder
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


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_driver_records_reconstructable_tool_turn(self) -> None:
        kernel = Kernel()
        await kernel.mount(RuntimePlugin())

        result = await AgentDriver(kernel).run("do the task", run_id="run-1")

        self.assertTrue(result.completed)
        self.assertEqual(result.content, "finished")
        self.assertEqual(result.steps, 2)
        self.assertEqual(
            [event.type for event in result.ledger.events],
            [
                "turn/start",
                "user/message",
                "step/start",
                "request/header",
                "assistant/message",
                "tool/call",
                "tool/result",
                "step/end",
                "step/start",
                "request/header",
                "assistant/message",
                "completion/checked",
                "step/end",
                "turn/end",
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

        result = await AgentDriver(kernel).run(
            "Write outputs/report.csv.",
            run_id="run-contract",
            task_id="public-task",
        )

        self.assertEqual(result.ledger.events[0].type, "task/contract-created")
        contract = result.ledger.events[0].payload["contract"]
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
