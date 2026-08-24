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


def _delivery_required_state(count: int = 1) -> dict[str, Any]:
    return {
        "messages": [
            types.SimpleNamespace(
                content=(
                    "[HARNESS DELIVERY REQUIRED] Read-only action blocked. "
                    "Write a required artifact first."
                ),
                type="human",
                id=f"previous-{index}",
            )
            for index in range(count)
        ]
    }


class DeerFlowToolActionLedgerMiddlewareTests(unittest.TestCase):
    def test_delivery_gate_blocks_non_output_script_write(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        called = False

        def handler(request):
            nonlocal called
            called = True
            return ToolMessage("written", tool_call_id=request.tool_call["id"])

        with bind_action_audit_sink(lambda _payload: None):
            result = middleware.wrap_tool_call(
                _request(
                    call_id="script-write",
                    name="write_file",
                    args={"path": "workspace/retry.py", "content": "print('retry')"},
                    turn=2,
                    state=_delivery_required_state(),
                ),
                handler,
            )

        self.assertFalse(called)
        self.assertEqual(result.status, "error")
        self.assertIn("non-output write blocked", result.content)

    def test_delivery_gate_keeps_environment_interaction_available(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        called = False

        def handler(request):
            nonlocal called
            called = True
            return ToolMessage("clicked", tool_call_id=request.tool_call["id"])

        with bind_action_audit_sink(lambda _payload: None):
            result = middleware.wrap_tool_call(
                _request(
                    call_id="browser-click",
                    name="browser_click",
                    args={"ref": "submit"},
                    turn=2,
                    state=_delivery_required_state(),
                ),
                handler,
            )

        self.assertTrue(called)
        self.assertEqual(result.content, "clicked")

    def test_object_runtime_context_run_id_keeps_epoch_monotonic_without_policy_session(self) -> None:
        from langgraph.prebuilt.tool_node import ToolCallRequest

        middleware = DeerFlowToolActionLedgerMiddleware()
        audits: list[dict[str, Any]] = []
        runtimes: list[Any] = []

        def request(call_id: str, name: str, args: dict[str, Any], turn: int):
            runtime = types.SimpleNamespace(
                context=types.SimpleNamespace(run_id="transport-run", turn=turn)
            )
            runtimes.append(runtime)
            return ToolCallRequest(
                tool_call={"id": call_id, "name": name, "args": args},
                state={},
                runtime=runtime,
            )

        with bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                request(
                    "write-1",
                    "write_file",
                    {"path": "outputs/report.json", "content": "{}"},
                    1,
                ),
                lambda raw: ToolMessage("written", tool_call_id=raw.tool_call["id"]),
            )
            middleware.wrap_tool_call(
                request("read-2", "read_file", {"path": "outputs/report.json"}, 2),
                lambda raw: ToolMessage("{}", tool_call_id=raw.tool_call["id"]),
            )

        self.assertEqual(len(runtimes), 2)
        self.assertNotEqual(id(runtimes[0]), id(runtimes[1]))
        self.assertEqual(
            [audit["record"]["mutation_epoch"] for audit in audits],
            [1, 1],
        )

    def test_policy_session_run_id_keeps_epoch_monotonic_for_object_runtime_contexts(self) -> None:
        from langgraph.prebuilt.tool_node import ToolCallRequest

        middleware = DeerFlowToolActionLedgerMiddleware()
        session = KernelPolicySession()
        ledger = SessionLedger("durable-run")
        session.start_contract(
            ledger,
            task_id="public-task",
            task_prompt="Write outputs/report.json.",
            public_schema=None,
        )
        audits: list[dict[str, Any]] = []
        runtimes: list[Any] = []

        def request(call_id: str, name: str, args: dict[str, Any], turn: int):
            runtime = types.SimpleNamespace(
                context=types.SimpleNamespace(run_id="transport-run", turn=turn)
            )
            runtimes.append(runtime)
            return ToolCallRequest(
                tool_call={"id": call_id, "name": name, "args": args},
                state={},
                runtime=runtime,
            )

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                request(
                    "write-1",
                    "write_file",
                    {"path": "outputs/report.json", "content": "{}"},
                    1,
                ),
                lambda raw: ToolMessage("written", tool_call_id=raw.tool_call["id"]),
            )

        session.begin_turn(ledger)
        with bind_policy_session(session), bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                request("read-2", "read_file", {"path": "outputs/report.json"}, 2),
                lambda raw: ToolMessage("{}", tool_call_id=raw.tool_call["id"]),
            )

        self.assertEqual(len(runtimes), 2)
        self.assertNotEqual(id(runtimes[0]), id(runtimes[1]))
        self.assertEqual(
            [audit["record"]["mutation_epoch"] for audit in audits],
            [1, 1],
        )

    def test_delivery_gate_allows_direct_synthesis_script_before_artifact_write(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        called = False

        def handler(request):
            nonlocal called
            called = True
            return ToolMessage("computed", tool_call_id=request.tool_call["id"])

        with bind_action_audit_sink(lambda _payload: None):
            result = middleware.wrap_tool_call(
                _request(
                    call_id="transform-1",
                    name="bash",
                    args={"command": "cd /task && python3 workspace/analysis/audit.py"},
                    turn=2,
                    state=_delivery_required_state(),
                ),
                handler,
            )

        self.assertTrue(called)
        self.assertEqual(result.content, "computed")
        self.assertIsNone(result.status)

    def test_textual_tool_error_does_not_advance_mutation_epoch(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()
        audits: list[dict[str, Any]] = []

        with bind_action_audit_sink(audits.append):
            middleware.wrap_tool_call(
                _request(
                    call_id="write-error",
                    name="bash",
                    args={"command": "mkdir -p /task/workspace/probe"},
                    turn=1,
                ),
                lambda request: ToolMessage(
                    "Error: Unsafe absolute paths in command: /probe",
                    tool_call_id=request.tool_call["id"],
                ),
            )
            middleware.wrap_tool_call(
                _request(
                    call_id="read-after-error",
                    name="read_file",
                    args={"path": "/task/workspace/input.json"},
                    turn=1,
                ),
                lambda request: ToolMessage("{}", tool_call_id=request.tool_call["id"]),
            )

        self.assertEqual(audits[0]["record"]["error_type"], "TOOL_ERROR")
        self.assertEqual(
            [audit["record"]["mutation_epoch"] for audit in audits],
            [0, 0],
        )

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
                    state=_delivery_required_state(),
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

        next_state = _delivery_required_state()
        next_state["messages"].append(
            ToolMessage(
                "[HARNESS DELIVERY REQUIRED] plain read blocked.",
                tool_call_id="old-blocked-read",
                status="error",
            )
        )
        with bind_policy_session(session), bind_action_audit_sink(lambda _payload: None):
            read_result = middleware.wrap_tool_call(
                _request(
                    call_id="read-2",
                    name="bash",
                    args={"command": "cat inputs/data.csv"},
                    turn=2,
                    state=next_state,
                ),
                handler,
            )

        self.assertTrue(called)
        self.assertEqual(read_result.content, "source rows")
        self.assertIsNone(read_result.status)

    def test_new_delivery_directive_rearms_gate_after_invalid_artifact(self) -> None:
        middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            middleware.wrap_tool_call(
                _request(
                    call_id="write-placeholder",
                    name="write_file",
                    args={"path": "outputs/report.json", "content": "{}"},
                    turn=1,
                    state=_delivery_required_state(),
                ),
                lambda request: ToolMessage("wrote", tool_call_id=request.tool_call["id"]),
            )

        called = False

        def handler(request):
            nonlocal called
            called = True
            return ToolMessage("stale source", tool_call_id=request.tool_call["id"])

        with bind_action_audit_sink(lambda _payload: None):
            result = middleware.wrap_tool_call(
                _request(
                    call_id="read-after-invalid-artifact",
                    name="read_file",
                    args={"path": "workspace/handoff.md"},
                    turn=2,
                    state=_delivery_required_state(2),
                ),
                handler,
            )

        self.assertFalse(called)
        self.assertEqual(result.status, "error")
        self.assertIn("[HARNESS DELIVERY REQUIRED]", result.content)

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


    def test_cacheable_local_read_budget_blocks_cross_tool_aliases_after_limit(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "3"}):
            middleware = DeerFlowToolActionLedgerMiddleware()
        audits: list[dict[str, Any]] = []
        calls = (
            ("read-1", "read_file", {"path": "/task/workspace/handoff.md"}),
            ("read-2", "bash", {"command": "cd /task/workspace && cat HANDOFF.md"}),
            ("read-3", "read_file", {"path": "/task/workspace/HANDOFF.md"}),
        )

        with bind_action_audit_sink(audits.append):
            for call_id, name, args in calls:
                result = middleware.wrap_tool_call(
                    _request(call_id=call_id, name=name, args=args, turn=1),
                    lambda request: ToolMessage("same", tool_call_id=request.tool_call["id"]),
                )
                self.assertIsNone(result.status)

            called = False

            def handler(request):
                nonlocal called
                called = True
                return ToolMessage("same", tool_call_id=request.tool_call["id"])

            blocked = middleware.wrap_tool_call(
                _request(
                    call_id="read-4",
                    name="bash",
                    args={"command": "cd /task/workspace && cat handoff.md"},
                    turn=2,
                ),
                handler,
            )

        self.assertFalse(called)
        self.assertEqual(blocked.status, "error")
        self.assertIn("Visible Evidence Workspace", blocked.content)
        self.assertEqual(audits[-1]["record"]["error_type"], "TOOL_ERROR")

    def test_cacheable_local_read_budget_allows_new_resource_and_resets_after_mutation(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            first = middleware.wrap_tool_call(
                _request(call_id="read-1", name="read_file", args={"path": "/task/workspace/a.md"}, turn=1),
                lambda request: ToolMessage("a", tool_call_id=request.tool_call["id"]),
            )
            mixed = middleware.wrap_tool_call(
                _request(
                    call_id="mixed-2",
                    name="bash",
                    args={"command": "cat /task/workspace/a.md /task/workspace/b.md"},
                    turn=1,
                ),
                lambda request: ToolMessage("a b", tool_call_id=request.tool_call["id"]),
            )
            blocked = middleware.wrap_tool_call(
                _request(call_id="read-3", name="read_file", args={"path": "/task/workspace/a.md"}, turn=1),
                lambda request: ToolMessage("a", tool_call_id=request.tool_call["id"]),
            )
            middleware.wrap_tool_call(
                _request(
                    call_id="write-4",
                    name="write_file",
                    args={"path": "/task/outputs/reset.txt", "content": "done"},
                    turn=1,
                ),
                lambda request: ToolMessage("wrote", tool_call_id=request.tool_call["id"]),
            )
            after_mutation = middleware.wrap_tool_call(
                _request(call_id="read-5", name="read_file", args={"path": "/task/workspace/a.md"}, turn=2),
                lambda request: ToolMessage("a", tool_call_id=request.tool_call["id"]),
            )

        self.assertIsNone(first.status)
        self.assertIsNone(mixed.status)
        self.assertEqual(blocked.status, "error")
        self.assertIsNone(after_mutation.status)


    def test_failed_local_reads_do_not_consume_successful_resource_budget(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "3"}):
            middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            for index in range(3):
                failed = middleware.wrap_tool_call(
                    _request(
                        call_id=f"failed-{index}",
                        name="read_file",
                        args={"path": "/task/workspace/failable.md"},
                        turn=index + 1,
                    ),
                    lambda request: ToolMessage(
                        f"missing-{index}",
                        tool_call_id=request.tool_call["id"],
                        status="error",
                    ),
                )
                self.assertEqual(failed.status, "error")

            called = False

            def handler(request):
                nonlocal called
                called = True
                return ToolMessage("data", tool_call_id=request.tool_call["id"])

            fourth = middleware.wrap_tool_call(
                _request(
                    call_id="success-4",
                    name="read_file",
                    args={"path": "/task/workspace/failable.md"},
                    turn=4,
                ),
                handler,
            )

        self.assertTrue(called)
        self.assertIsNone(fourth.status)

    def test_mutation_while_read_inflight_prevents_stale_reservation_commit(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            request = _request(
                call_id="read-before-mutation",
                name="read_file",
                args={"path": "/task/workspace/stale.md"},
                turn=1,
            )
            self.assertIsNone(middleware._blocked_result(request))
            mutation = middleware.wrap_tool_call(
                _request(
                    call_id="write-during-read",
                    name="write_file",
                    args={"path": "/task/outputs/reset.txt", "content": "done"},
                    turn=1,
                ),
                lambda tool_request: ToolMessage("wrote", tool_call_id=tool_request.tool_call["id"]),
            )
            self.assertIsNone(mutation.status)
            observed = middleware._observe(
                request,
                ToolMessage("stale data", tool_call_id="read-before-mutation"),
            )
            next_read = middleware.wrap_tool_call(
                _request(
                    call_id="read-after-mutation",
                    name="read_file",
                    args={"path": "/task/workspace/stale.md"},
                    turn=2,
                ),
                lambda tool_request: ToolMessage("fresh data", tool_call_id=tool_request.tool_call["id"]),
            )

        self.assertIsNone(observed.status)
        self.assertIsNone(next_read.status)


    def test_second_local_cache_block_ends_current_turn_and_audits_error(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()
        audits: list[dict[str, Any]] = []

        with bind_action_audit_sink(audits.append):
            first = middleware.wrap_tool_call(
                _request(call_id="read-1", name="read_file", args={"path": "/task/workspace/end.md"}, turn=1),
                lambda request: ToolMessage("data", tool_call_id=request.tool_call["id"]),
            )
            first_block = middleware.wrap_tool_call(
                _request(call_id="block-1", name="read_file", args={"path": "/task/workspace/end.md"}, turn=1),
                lambda request: ToolMessage("should not run", tool_call_id=request.tool_call["id"]),
            )
            second_block = middleware.wrap_tool_call(
                _request(call_id="block-2", name="read_file", args={"path": "/task/workspace/end.md"}, turn=1),
                lambda request: ToolMessage("should not run", tool_call_id=request.tool_call["id"]),
            )

        self.assertIsNone(first.status)
        self.assertEqual(first_block.status, "error")
        self.assertIsInstance(second_block, Command)
        self.assertEqual(second_block.goto, "__end__")
        message = second_block.update["messages"][0]
        self.assertEqual(message.status, "error")
        self.assertIn("Policy recovery", message.content)
        self.assertEqual(audits[-1]["record"]["error_type"], "TOOL_ERROR")

    def test_local_cache_block_count_resets_next_turn(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            middleware.wrap_tool_call(
                _request(call_id="read-1", name="read_file", args={"path": "/task/workspace/reset.md"}, turn=1),
                lambda request: ToolMessage("data", tool_call_id=request.tool_call["id"]),
            )
            turn_one_block = middleware.wrap_tool_call(
                _request(call_id="block-1", name="read_file", args={"path": "/task/workspace/reset.md"}, turn=1),
                lambda request: ToolMessage("should not run", tool_call_id=request.tool_call["id"]),
            )
            turn_two_block = middleware.wrap_tool_call(
                _request(call_id="block-2", name="read_file", args={"path": "/task/workspace/reset.md"}, turn=2),
                lambda request: ToolMessage("should not run", tool_call_id=request.tool_call["id"]),
            )

        self.assertEqual(turn_one_block.status, "error")
        self.assertEqual(turn_two_block.status, "error")
        self.assertNotIsInstance(turn_two_block, Command)

    def test_cacheable_local_read_budget_does_not_block_http_resources(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "1"}):
            middleware = DeerFlowToolActionLedgerMiddleware()

        with bind_action_audit_sink(lambda _payload: None):
            for index in range(2):
                result = middleware.wrap_tool_call(
                    _request(
                        call_id=f"http-{index}",
                        name="bash",
                        args={"command": "curl https://public.example.com/data?key=keep"},
                        turn=index + 1,
                    ),
                    lambda request: ToolMessage("remote", tool_call_id=request.tool_call["id"]),
                )
                self.assertIsNone(result.status)

    def test_cacheable_local_read_budget_is_consistent_under_concurrent_reads(self) -> None:
        with patch.dict(os.environ, {"ADAPTIVE_MAX_READS_PER_RESOURCE": "3"}):
            middleware = DeerFlowToolActionLedgerMiddleware()
        audit_lock = Lock()
        audits: list[dict[str, Any]] = []

        def record(payload: dict[str, Any]) -> None:
            with audit_lock:
                audits.append(payload)

        def execute(index: int) -> str | None:
            with bind_action_audit_sink(record):
                result = middleware.wrap_tool_call(
                    _request(
                        call_id=f"concurrent-{index}",
                        name="read_file",
                        args={"path": "/task/workspace/concurrent.md"},
                        turn=1,
                    ),
                    lambda request: ToolMessage("data", tool_call_id=request.tool_call["id"]),
                )
                return "end" if isinstance(result, Command) else result.status

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(execute, range(8)))

        self.assertEqual(statuses.count(None), 3)
        self.assertEqual(statuses.count("error") + statuses.count("end"), 5)
        self.assertGreaterEqual(statuses.count("end"), 1)
        self.assertEqual(len(audits), 8)

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
