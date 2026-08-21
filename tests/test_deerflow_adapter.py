from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from adaptive_harness.integrations.deerflow import (
    DeerFlowEventAdapter,
    DeerFlowRunRequest,
    DeerFlowRuntimeAdapter,
)
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    FileArtifactObservationProvider,
    HttpJsonMatchObservationProvider,
    StructuredDeerFlowObservationProvider,
)
from adaptive_harness.ledger import SessionLedger


class DeerFlowAdapterTests(unittest.TestCase):
    def test_repeated_values_are_idempotent_and_end_usage_is_authoritative(self) -> None:
        messages = [
            {"type": "human", "id": "u1", "content": "do it"},
            {
                "type": "ai",
                "id": "a1",
                "content": "working",
                "usage_metadata": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
                "tool_calls": [{"id": "c1", "name": "bash", "args": {"command": "pwd"}}],
            },
            {
                "type": "tool",
                "id": "t1",
                "tool_call_id": "c1",
                "name": "bash",
                "content": "/task",
            },
            {"type": "ai", "id": "a2", "content": "done"},
        ]
        events = [
            {"type": "messages-tuple", "data": {"type": "ai", "id": "a1", "content": "work"}},
            {"type": "messages-tuple", "data": {"type": "ai", "id": "a1", "content": "ing"}},
            {"type": "values", "data": {"messages": messages}},
            {"type": "values", "data": {"messages": messages}},
            {
                "type": "end",
                "data": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    }
                },
            },
        ]
        ledger = SessionLedger("complete")

        summary = DeerFlowEventAdapter().replay(ledger, events)

        self.assertEqual(summary.response_text, "done")
        self.assertEqual(len(summary.tool_calls), 1)
        self.assertEqual(len(summary.tool_results), 1)
        self.assertEqual(summary.usage["total_tokens"], 120)
        self.assertEqual(sum(event.type == "assistant/chunk" for event in ledger.events), 2)
        self.assertEqual(sum(event.type == "tool/call" for event in ledger.events), 1)
        self.assertEqual(sum(event.type == "tool/result" for event in ledger.events), 1)
        self.assertEqual(ledger.events[-1].type, "runtime/end")
        self.assertEqual(ledger.events[-1].payload["source"], "deerflow")

    def test_partial_run_recovers_unique_message_usage(self) -> None:
        first = {
            "type": "ai",
            "id": "a1",
            "content": "first",
            "usage_metadata": {
                "input_tokens": 4,
                "output_tokens": 1,
                "total_tokens": 5,
            },
        }
        second = {
            "type": "ai",
            "id": "a2",
            "content": "partial",
            "usage_metadata": {
                "input_tokens": 6,
                "output_tokens": 2,
                "total_tokens": 8,
            },
        }
        events = [
            {"type": "values", "data": {"messages": [first]}},
            {"type": "values", "data": {"messages": [first, second]}},
            {"type": "values", "data": {"messages": [first, second]}},
        ]
        ledger = SessionLedger("partial")

        summary = DeerFlowEventAdapter().replay(ledger, events)

        self.assertEqual(summary.response_text, "partial")
        self.assertEqual(
            summary.usage,
            {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        )
        self.assertEqual(ledger.events[-1].type, "runtime/end")
        self.assertEqual(ledger.events[-1].payload["source"], "deerflow-recovered")


@dataclass
class _RawEvent:
    type: str
    data: dict[str, Any]


class _FakeEnvironment:
    def __init__(self) -> None:
        self.built = False
        self.cleaned = False

    async def build(self) -> None:
        self.built = True

    async def cleanup(self) -> None:
        self.cleaned = True

    def state(self) -> dict[str, Any]:
        return {"workspace": "/task"}

    async def tools(self) -> list[Any]:
        return []


class _FakeClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.received: tuple[str, str | None, dict[str, Any]] | None = None

    def stream(self, message: str, *, thread_id: str | None = None, **kwargs: Any):
        self.received = (message, thread_id, kwargs)
        yield _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"})
        if self.error:
            raise self.error
        yield _RawEvent("end", {"usage": {"total_tokens": 3}})


class _TurnClient:
    def __init__(self, turns: list[list[_RawEvent]]) -> None:
        self.turns = turns
        self.calls: list[tuple[str, str | None]] = []

    def stream(self, message: str, *, thread_id: str | None = None, **kwargs: Any):
        index = len(self.calls)
        self.calls.append((message, thread_id))
        yield from self.turns[index]


class DeerFlowRuntimeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_snapshots_request_and_cleans_environment(self) -> None:
        environment = _FakeEnvironment()
        client = _FakeClient()
        adapter = DeerFlowRuntimeAdapter(client, environment)
        request = DeerFlowRunRequest(
            message="finish task",
            thread_id="thread-1",
            client_options={"model_name": "deepseek-v4-flash"},
            tool_schemas=[{"name": "bash", "description": "run command"}],
            context={"profile": "baseline"},
        )

        result = await adapter.run(request, run_id="run-1")

        header = result.ledger.events[0]
        self.assertTrue(environment.built)
        self.assertTrue(environment.cleaned)
        self.assertEqual(client.received, ("finish task", "thread-1", dict(request.client_options)))
        self.assertEqual(header.type, "request/header")
        self.assertEqual(header.payload["tools"][0]["name"], "bash")
        self.assertEqual(header.payload["context"]["environment"]["workspace"], "/task")
        self.assertEqual(result.summary.response_text, "done")
        self.assertEqual(result.summary.usage, {"total_tokens": 3})

    async def test_runtime_preserves_partial_ledger_and_cleans_on_error(self) -> None:
        environment = _FakeEnvironment()
        adapter = DeerFlowRuntimeAdapter(_FakeClient(error=RuntimeError("stream failed")), environment)

        with TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "events.jsonl"
            with self.assertRaisesRegex(RuntimeError, "stream failed"):
                await adapter.run(
                    DeerFlowRunRequest("task", "thread-2"),
                    run_id="run-2",
                    ledger_path=ledger_path,
                )
            event_types = [event.type for event in SessionLedger.replay(ledger_path).events]

        self.assertTrue(environment.cleaned)
        self.assertEqual(event_types[-2:], ["runtime/error", "runtime/end"])

    async def test_runtime_rejects_credentials_in_recorded_options(self) -> None:
        environment = _FakeEnvironment()
        adapter = DeerFlowRuntimeAdapter(_FakeClient(), environment)

        with self.assertRaisesRegex(ValueError, "credentials through the environment"):
            await adapter.run(
                DeerFlowRunRequest(
                    "task",
                    "thread-3",
                    client_options={"api_key": "must-not-be-recorded"},
                )
            )

        self.assertFalse(environment.built)

    async def test_policy_bridge_continues_same_thread_until_structured_evidence(self) -> None:
        client = _TurnClient(
            [
                [
                    _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                    _RawEvent(
                        "values",
                        {"messages": [{"type": "ai", "id": "a1", "content": "done"}]},
                    ),
                    _RawEvent("end", {"usage": {"total_tokens": 10}}),
                ],
                [
                    _RawEvent(
                        "messages-tuple",
                        {
                            "type": "tool",
                            "id": "tool-1",
                            "tool_call_id": "call-1",
                            "name": "submit_listing",
                            "content": "ok",
                            "artifact": {
                                "adaptive_evidence": [
                                    {
                                        "kind": "observation",
                                        "subject": "listing.submitted",
                                        "value": True,
                                    }
                                ]
                            },
                        },
                    ),
                    _RawEvent("messages-tuple", {"type": "ai", "id": "a2", "content": "done"}),
                    _RawEvent(
                        "values",
                        {
                            "messages": [
                                {"type": "ai", "id": "a1", "content": "done"},
                                {
                                    "type": "tool",
                                    "id": "tool-1",
                                    "tool_call_id": "call-1",
                                    "name": "submit_listing",
                                    "content": "ok",
                                },
                                {"type": "ai", "id": "a2", "content": "done"},
                            ]
                        },
                    ),
                    _RawEvent("end", {"usage": {"total_tokens": 20}}),
                ],
            ]
        )
        bridge = DeerFlowPolicyBridge(
            observation_providers=(StructuredDeerFlowObservationProvider(),),
            max_completion_turns=2,
        )
        adapter = DeerFlowRuntimeAdapter(client, _FakeEnvironment(), policy_bridge=bridge)

        result = await adapter.run(
            DeerFlowRunRequest(
                "帮我把商品发上线，发品系统打开后提交。",
                "thread-policy",
                task_id="listing-task",
            ),
            run_id="run-policy",
        )

        event_types = [event.type for event in result.ledger.events]
        checks = [event for event in result.ledger.events if event.type == "completion/checked"]
        self.assertTrue(result.completed)
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.summary.usage, {"total_tokens": 30})
        self.assertEqual(client.calls[0][1], client.calls[1][1])
        self.assertIn("Completion rejected by task evidence", client.calls[1][0])
        self.assertEqual(event_types.count("runtime/turn-end"), 2)
        self.assertEqual(event_types.count("runtime/end"), 1)
        self.assertEqual(event_types.count("evidence/added"), 1)
        self.assertEqual(event_types.count("assistant/message"), 2)
        self.assertEqual(event_types.count("tool/result"), 1)
        self.assertFalse(checks[0].payload["passed"])
        self.assertTrue(checks[1].payload["passed"])

    async def test_policy_bridge_fails_closed_when_turn_budget_expires(self) -> None:
        client = _TurnClient(
            [[
                _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                _RawEvent("end", {"usage": {"total_tokens": 5}}),
            ]]
        )
        bridge = DeerFlowPolicyBridge(max_completion_turns=1)

        result = await DeerFlowRuntimeAdapter(
            client,
            _FakeEnvironment(),
            policy_bridge=bridge,
        ).run(
            DeerFlowRunRequest("Write outputs/report.csv.", "thread-budget"),
            run_id="run-budget",
        )

        self.assertFalse(result.completed)
        self.assertEqual(result.turns, 1)
        self.assertEqual(result.ledger.events[-1].payload["reason"], "completion_rejected")

    async def test_filesystem_provider_hashes_required_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/report.csv"
            output.parent.mkdir()
            output.write_text("header\nvalue\n")
            client = _TurnClient(
                [[
                    _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                    _RawEvent("end", {"usage": {"total_tokens": 5}}),
                ]]
            )
            bridge = DeerFlowPolicyBridge(
                observation_providers=(FileArtifactObservationProvider(root),),
            )

            result = await DeerFlowRuntimeAdapter(
                client,
                _FakeEnvironment(),
                policy_bridge=bridge,
            ).run(
                DeerFlowRunRequest("Write outputs/report.csv.", "thread-file"),
                run_id="run-file-policy",
            )

        evidence = next(event for event in result.ledger.events if event.type == "evidence/added")
        self.assertTrue(result.completed)
        self.assertTrue(evidence.payload["evidence"]["value"]["exists"])
        self.assertEqual(len(evidence.payload["evidence"]["value"]["sha256"]), 64)

    async def test_loopback_http_provider_projects_public_early_terminate_signal(self) -> None:
        provider = HttpJsonMatchObservationProvider(
            url="http://127.0.0.1:3000/api/sessions",
            subject="listing.submitted",
            match_kind="list_any_field_equals",
            match_field="status",
            match_value="submitted",
            fetch_json=lambda _url: [{"status": "submitted"}],
        )
        client = _TurnClient(
            [[
                _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                _RawEvent("end", {"usage": {"total_tokens": 5}}),
            ]]
        )
        result = await DeerFlowRuntimeAdapter(
            client,
            _FakeEnvironment(),
            policy_bridge=DeerFlowPolicyBridge(observation_providers=(provider,)),
        ).run(
            DeerFlowRunRequest("帮我把商品发上线，发品系统打开后提交。", "thread-http"),
            run_id="run-http",
        )

        self.assertTrue(result.completed)
        evidence = next(event for event in result.ledger.events if event.type == "evidence/added")
        self.assertEqual(evidence.payload["evidence"]["subject"], "listing.submitted")
        self.assertTrue(evidence.payload["evidence"]["value"])


if __name__ == "__main__":
    unittest.main()
