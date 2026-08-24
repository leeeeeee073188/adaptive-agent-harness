from __future__ import annotations

import os
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Any
from unittest.mock import patch

from adaptive_harness.action_audit import bind_action_audit_sink
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.policy_session import KernelPolicySession, bind_policy_session


def _install_fake_deerflow_tool_modules() -> None:
    if "langgraph.prebuilt.tool_node" in sys.modules:
        return

    langchain = types.ModuleType("langchain")
    agents = types.ModuleType("langchain.agents")
    middleware = types.ModuleType("langchain.agents.middleware")
    core = types.ModuleType("langchain_core")
    messages = types.ModuleType("langchain_core.messages")
    langgraph = types.ModuleType("langgraph")
    graph = types.ModuleType("langgraph.graph")
    prebuilt = types.ModuleType("langgraph.prebuilt")
    tool_node = types.ModuleType("langgraph.prebuilt.tool_node")
    graph_types = types.ModuleType("langgraph.types")

    class AgentMiddleware:  # noqa: D401 - fake optional dependency base.
        pass

    class ToolCallLimitMiddleware:  # noqa: D401 - fake optional dependency base.
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs

    class ToolMessage:
        def __init__(self, content: Any = "", tool_call_id: str = "", status: str | None = None) -> None:
            self.content = content
            self.tool_call_id = tool_call_id
            self.status = status

        def model_copy(self, *, update: dict[str, Any]) -> ToolMessage:
            copied = ToolMessage(self.content, self.tool_call_id, self.status)
            for key, value in update.items():
                setattr(copied, key, value)
            return copied

    class Command:
        def __init__(self, *, update: dict[str, Any] | None = None, goto: Any = None) -> None:
            self.update = update
            self.goto = goto

    @dataclass(frozen=True)
    class ToolCallRequest:
        tool_call: dict[str, Any]
        state: dict[str, Any]
        runtime: Any

    middleware.AgentMiddleware = AgentMiddleware
    middleware.ToolCallLimitMiddleware = ToolCallLimitMiddleware
    messages.ToolMessage = ToolMessage
    graph.END = "__end__"
    graph_types.Command = Command
    tool_node.ToolCallRequest = ToolCallRequest

    sys.modules.update(
        {
            "langchain": langchain,
            "langchain.agents": agents,
            "langchain.agents.middleware": middleware,
            "langchain_core": core,
            "langchain_core.messages": messages,
            "langgraph": langgraph,
            "langgraph.graph": graph,
            "langgraph.prebuilt": prebuilt,
            "langgraph.prebuilt.tool_node": tool_node,
            "langgraph.types": graph_types,
        }
    )


_install_fake_deerflow_tool_modules()

from langchain_core.messages import ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

import adaptive_harness.integrations.deerflow_action_ledger as action_ledger_module  # noqa: E402
from adaptive_harness.integrations.deerflow_action_ledger import (  # noqa: E402
    DeerFlowToolActionLedgerMiddleware,
)


@dataclass(frozen=True)
class _Runtime:
    context: dict[str, Any]


def _request(
    *,
    call_id: str,
    name: str,
    args: dict[str, Any],
    turn: int,
    state: dict[str, Any] | None = None,
    run_id: str = "run-1",
):
    from langgraph.prebuilt.tool_node import ToolCallRequest

    return ToolCallRequest(
        tool_call={"id": call_id, "name": name, "args": args},
        state=state or {},
        runtime=_Runtime({"run_id": run_id, "thread_id": f"{run_id}-thread", "turn": turn}),
    )


def _delivery_required_state() -> dict[str, Any]:
    return {
        "messages": [
            ToolMessage(
                "[HARNESS DELIVERY REQUIRED] Read-only action blocked. Write a required artifact first.",
                tool_call_id="previous",
                status="error",
            )
        ]
    }


class DeerFlowToolActionLedgerMiddlewareTests(unittest.TestCase):
    def test_delivery_write_satisfies_next_turn_delivery_gate_for_same_run(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        session = KernelPolicySession()
        ledger = SessionLedger("run-1")
        session.begin_turn(ledger)

        with bind_policy_session(session), bind_action_audit_sink(lambda _payload: None):
            write_result = middleware.wrap_tool_call(
                _request(
                    call_id="write-1",
                    name="write_file",
                    args={"path": "outputs/report.csv", "content": "partial"},
                    turn=1,
                ),
                lambda request: ToolMessage("wrote", tool_call_id=request.tool_call["id"]),
            )

        self.assertIsNone(write_result.status)

        session.begin_turn(ledger)
        called = False

        def handler(request):
            nonlocal called
            called = True
            return ToolMessage("source rows", tool_call_id=request.tool_call["id"])

        with bind_policy_session(session), bind_action_audit_sink(lambda _payload: None):
            read_result = middleware.wrap_tool_call(
                _request(
                    call_id="read-2",
                    name="bash",
                    args={"command": "cat inputs/data.csv"},
                    turn=2,
                    state=_delivery_required_state(),
                ),
                handler,
            )

        self.assertTrue(called)
        self.assertEqual(read_result.content, "source rows")
        self.assertIsNone(read_result.status)

    def test_action_ledger_mutation_epoch_is_monotonic_across_turns_for_same_run(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        session = KernelPolicySession()
        ledger = SessionLedger("run-1")
        audits: list[dict[str, Any]] = []

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                _request(
                    call_id="write-1",
                    name="write_file",
                    args={"path": "outputs/report.csv", "content": "partial"},
                    turn=1,
                ),
                lambda request: ToolMessage("wrote", tool_call_id=request.tool_call["id"]),
            )

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                _request(
                    call_id="read-2",
                    name="bash",
                    args={"command": "cat inputs/data.csv"},
                    turn=2,
                ),
                lambda request: ToolMessage("rows", tool_call_id=request.tool_call["id"]),
            )

        epochs = [payload["record"]["mutation_epoch"] for payload in audits]
        self.assertEqual(epochs, [1, 1])


    def test_parallel_same_run_observations_have_consistent_sequences_and_attempts(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        audit_lock = Lock()
        audits: list[dict[str, Any]] = []

        def record(payload: dict[str, Any]) -> None:
            with audit_lock:
                audits.append(payload)

        def execute(index: int) -> None:
            with bind_action_audit_sink(record):
                middleware.wrap_tool_call(
                    _request(
                        call_id=f"read-{index}",
                        name="bash",
                        args={"command": "cat inputs/data.csv"},
                        turn=1,
                    ),
                    lambda request: ToolMessage("same rows", tool_call_id=request.tool_call["id"]),
                )

        total = 12
        with ThreadPoolExecutor(max_workers=total) as executor:
            list(executor.map(execute, range(total)))

        sequences = sorted(payload["record"]["sequence"] for payload in audits)
        attempts = sorted(payload["decision"]["scope_attempts"] for payload in audits)
        self.assertEqual(sequences, list(range(1, total + 1)))
        self.assertEqual(attempts, list(range(1, total + 1)))

    def test_lru_eviction_discards_delivery_state_for_evicted_run(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()

        with patch.object(action_ledger_module, "_MAX_RUN_LEDGERS", 1):
            with bind_action_audit_sink(lambda _payload: None):
                middleware.wrap_tool_call(
                    _request(
                        call_id="write-run-1",
                        name="write_file",
                        args={"path": "outputs/report.csv", "content": "partial"},
                        turn=1,
                        run_id="run-1",
                    ),
                    lambda request: ToolMessage("wrote", tool_call_id=request.tool_call["id"]),
                )
                middleware.wrap_tool_call(
                    _request(
                        call_id="read-run-2",
                        name="bash",
                        args={"command": "cat other.txt"},
                        turn=1,
                        run_id="run-2",
                    ),
                    lambda request: ToolMessage("other", tool_call_id=request.tool_call["id"]),
                )

                called = False

                def handler(request):
                    nonlocal called
                    called = True
                    return ToolMessage("source rows", tool_call_id=request.tool_call["id"])

                result = middleware.wrap_tool_call(
                    _request(
                        call_id="read-run-1",
                        name="bash",
                        args={"command": "cat inputs/data.csv"},
                        turn=2,
                        state=_delivery_required_state(),
                        run_id="run-1",
                    ),
                    handler,
                )

        self.assertFalse(called)
        self.assertEqual(result.status, "error")
        self.assertIn("[HARNESS DELIVERY REQUIRED]", result.content)

    def test_nonmutating_action_budget_resets_each_policy_turn(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_NONMUTATING_ACTIONS_PER_TURN": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()
        session = KernelPolicySession()
        ledger = SessionLedger("run-1")

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(lambda _payload: None):
            first = middleware.wrap_tool_call(
                _request(call_id="read-1", name="bash", args={"command": "cat a.txt"}, turn=1),
                lambda request: ToolMessage("a", tool_call_id=request.tool_call["id"]),
            )
            second = middleware.wrap_tool_call(
                _request(call_id="read-2", name="bash", args={"command": "cat b.txt"}, turn=1),
                lambda request: ToolMessage("b", tool_call_id=request.tool_call["id"]),
            )

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(lambda _payload: None):
            third = middleware.wrap_tool_call(
                _request(call_id="read-3", name="bash", args={"command": "cat c.txt"}, turn=2),
                lambda request: ToolMessage("c", tool_call_id=request.tool_call["id"]),
            )

        self.assertEqual(first.content, "a")
        self.assertIsInstance(second, Command)
        self.assertEqual(third.content, "c")


if __name__ == "__main__":
    unittest.main()
