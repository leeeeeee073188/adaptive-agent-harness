from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass, replace
from typing import Any

from adaptive_harness.context_audit import bind_context_audit_sink
from adaptive_harness.policy_session import KernelPolicySession, bind_policy_session


def _install_fake_langchain_modules() -> None:
    if "langchain.agents.middleware.types" in sys.modules:
        return

    langchain = types.ModuleType("langchain")
    agents = types.ModuleType("langchain.agents")
    middleware = types.ModuleType("langchain.agents.middleware")
    middleware_types = types.ModuleType("langchain.agents.middleware.types")
    core = types.ModuleType("langchain_core")
    messages = types.ModuleType("langchain_core.messages")

    class AgentMiddleware:  # noqa: D401 - fake external base for optional integration tests.
        pass

    class BaseMessage:
        def __init__(self, content: Any = "", **kwargs: Any) -> None:
            self.content = content
            self.additional_kwargs = dict(kwargs.get("additional_kwargs") or {})
            self.response_metadata = dict(kwargs.get("response_metadata") or {})
            self.name = kwargs.get("name")
            self.id = kwargs.get("id")
            self.type = kwargs.get("type", "base")

    class HumanMessage(BaseMessage):
        def __init__(self, content: Any = "", **kwargs: Any) -> None:
            super().__init__(content, **kwargs)
            self.type = "human"

    class SystemMessage(BaseMessage):
        def __init__(self, content: Any = "", **kwargs: Any) -> None:
            super().__init__(content, **kwargs)
            self.type = "system"

    class AIMessage(BaseMessage):
        def __init__(self, content: Any = "", **kwargs: Any) -> None:
            super().__init__(content, **kwargs)
            self.type = "ai"
            self.tool_calls = kwargs.get("tool_calls") or []

    class ToolMessage(BaseMessage):
        def __init__(self, content: Any = "", tool_call_id: str = "", **kwargs: Any) -> None:
            super().__init__(content, **kwargs)
            self.type = "tool"
            self.tool_call_id = tool_call_id

    @dataclass(frozen=True)
    class ModelRequest:
        messages: list[Any]
        system_message: Any | None = None
        state: dict[str, Any] | None = None

        def override(self, **kwargs: Any) -> ModelRequest:
            return replace(self, **kwargs)

    class ModelResponse:  # noqa: D401 - fake external response type.
        pass

    middleware.AgentMiddleware = AgentMiddleware
    middleware.ToolCallLimitMiddleware = object
    middleware_types.ModelRequest = ModelRequest
    middleware_types.ModelResponse = ModelResponse
    middleware_types.ModelCallResult = object
    messages.BaseMessage = BaseMessage
    messages.HumanMessage = HumanMessage
    messages.SystemMessage = SystemMessage
    messages.AIMessage = AIMessage
    messages.ToolMessage = ToolMessage

    sys.modules.update(
        {
            "langchain": langchain,
            "langchain.agents": agents,
            "langchain.agents.middleware": middleware,
            "langchain.agents.middleware.types": middleware_types,
            "langchain_core": core,
            "langchain_core.messages": messages,
        }
    )


class DeerFlowContextMiddlewareTests(unittest.TestCase):
    def test_bound_policy_task_state_reaches_prepared_context(self) -> None:
        _install_fake_langchain_modules()
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import HumanMessage

        from adaptive_harness.integrations.deerflow_context import DeerFlowTaskAwareContextMiddleware

        session = KernelPolicySession()
        middleware = DeerFlowTaskAwareContextMiddleware()
        captured = {}
        task_state = {
            "task": {
                "failures": [{"id": "f1", "error_type": "INVALID_EVIDENCE", "message": "provider failed"}],
                "latest_completion": {"passed": False, "missing": ["outputs/report.csv"]},
                "recent_recoveries": [{"primary": "artifact_error"}],
            }
        }

        def handler(request):
            captured["system"] = getattr(request.system_message, "content", "")
            captured["messages"] = [getattr(message, "content", "") for message in request.messages]
            return object()

        with bind_policy_session(session, task_state=task_state):
            middleware.wrap_model_call(
                ModelRequest(messages=[HumanMessage(content="Write outputs/report.csv.")]),
                handler,
            )

        rendered = "\n".join(str(item) for item in captured["messages"] + [captured["system"]])
        self.assertIn("INVALID_EVIDENCE", rendered)
        self.assertIn("latest_completion", rendered)
        self.assertIn("recent_recoveries", rendered)
        self.assertIn("HARNESS_CONTEXT_AUTHORITY", rendered)

    def test_selector_fallback_is_safe_minimal_and_drops_tool_history(self) -> None:
        _install_fake_langchain_modules()
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        from adaptive_harness.integrations.deerflow_context import DeerFlowTaskAwareContextMiddleware

        class BrokenContextManager:
            def prepare(self, messages, *, environment_state, task_state):
                raise ValueError("synthetic selector failure with api_key=plain-api-key")

        middleware = DeerFlowTaskAwareContextMiddleware()
        session = KernelPolicySession(context_manager=BrokenContextManager())
        captured = {}
        audits = []

        def handler(request):
            captured["messages"] = request.messages
            captured["system"] = request.system_message
            return object()

        messages = [
            SystemMessage(content="system password=systempass"),
            HumanMessage(content="First task with token budget note."),
            AIMessage(
                content="calling tool",
                tool_calls=[{"id": "c1", "name": "read_file", "args": {"password": "plainpass"}}],
            ),
            ToolMessage(
                content="huge secret tool content api_key=plain-api-key " + "x" * 2000,
                tool_call_id="c1",
            ),
            HumanMessage(content="Latest human says continue; Authorization: Bearer plainbearer123"),
        ]

        with bind_policy_session(session), bind_context_audit_sink(audits.append):
            middleware.wrap_model_call(ModelRequest(messages=messages), handler)

        rendered = "\n".join(str(getattr(message, "content", "")) for message in captured["messages"]).lower()
        self.assertLessEqual(len(captured["messages"]), 3)
        self.assertTrue(all(not isinstance(message, (AIMessage, ToolMessage)) for message in captured["messages"]))
        self.assertIn("first task", rendered)
        self.assertIn("latest human", rendered)
        self.assertIn("token budget", rendered)
        self.assertNotIn("plainpass", rendered)
        self.assertNotIn("plain-api-key", rendered)
        self.assertNotIn("plainbearer123", rendered)
        self.assertNotIn("huge secret tool content", rendered)
        self.assertTrue(audits)
        self.assertTrue(audits[-1]["safe_fallback"])
        self.assertTrue(audits[-1]["dropped_tool_history"])
        self.assertNotIn("plain-api-key", str(audits).lower())

    def test_selected_deerflow_history_uses_sanitized_messages_not_originals(self) -> None:
        _install_fake_langchain_modules()
        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from adaptive_harness.integrations.deerflow_context import DeerFlowTaskAwareContextMiddleware

        middleware = DeerFlowTaskAwareContextMiddleware()
        captured = {}

        def handler(request):
            captured["messages"] = request.messages
            return object()

        messages = [
            HumanMessage(content="Complete with token budget."),
            AIMessage(
                content="calling tool",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "read_file",
                        "args": {"path": "/task/config.txt", "client_secret": "plain-client-secret"},
                    }
                ],
            ),
            ToolMessage(content="password=plainpass\nTITLE: safe fact", tool_call_id="c1"),
        ]

        middleware.wrap_model_call(ModelRequest(messages=messages), handler)

        rendered = "\n".join(str(getattr(message, "content", "")) for message in captured["messages"]).lower()
        self.assertNotIn("plain-client-secret", str(captured["messages"]).lower())
        self.assertNotIn("plainpass", rendered)
        self.assertIn("safe fact", rendered)
        tool_messages = [message for message in captured["messages"] if isinstance(message, ToolMessage)]
        ai_messages = [message for message in captured["messages"] if isinstance(message, AIMessage)]
        self.assertEqual(tool_messages[0].tool_call_id, "c1")
        self.assertEqual(ai_messages[0].tool_calls[0]["id"], "c1")
        self.assertIn("args", ai_messages[0].tool_calls[0])
        self.assertNotIn("arguments", ai_messages[0].tool_calls[0])
        self.assertEqual(ai_messages[0].tool_calls[0]["args"]["client_secret"], "<redacted>")


if __name__ == "__main__":
    unittest.main()
