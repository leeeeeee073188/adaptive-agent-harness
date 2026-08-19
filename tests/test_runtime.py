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
from adaptive_harness.services import COMPLETION_POLICY, CONTEXT_MANAGER, ENVIRONMENT, MODEL, TOOL_RUNTIME
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
