from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from adaptive_harness.integrations.deerflow import (
    DeerFlowEventAdapter,
    DeerFlowReplaySummary,
    DeerFlowRunRequest,
    DeerFlowRuntimeAdapter,
)
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    FileArtifactObservationProvider,
    HttpJsonMatchObservationProvider,
    OutputFileCountObservationProvider,
    StructuredDeerFlowObservationProvider,
)
from adaptive_harness.integrations.realreplica_contract import realreplica_contract_builder
from adaptive_harness.integrations.realreplica_observations import (
    GmailMcpObservationProvider,
    GoogleDocsMcpChangeObservationProvider,
    WorkbenchCalendarObservationProvider,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.model_routes import PRIMARY_MODEL
from adaptive_harness.progress import ProgressResult, ProgressStatus, RuleBasedProgressDetector
from adaptive_harness.recovery import (
    RuleBasedRecoveryOutcomeEvaluator,
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
    TaskRecoveryAction,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder
from adaptive_harness.task_state import TaskStateProjector


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

    def test_gmail_mcp_provider_reads_label_and_calendar_postconditions(self) -> None:
        def call_tool(_endpoint: str, name: str, _arguments: dict[str, Any]):
            if name == "gmail.listLabels":
                return {"labels": [{"name": "VBR-52"}]}
            if name == "calendar.listEvents":
                return {"events": [{"title": "VBR-52 Harbor Stitch"}]}
            raise AssertionError(name)

        contract = realreplica_contract_builder().build(
            "gmail",
            "创建顶层标签 `VBR-52`，再建一个 `VBR-52 Harbor Stitch` 日历事件。",
        )
        provider = GmailMcpObservationProvider(
            "http://127.0.0.1:3071/mcp",
            call_tool=call_tool,
        )
        evidence = provider.observe(
            contract,
            DeerFlowReplaySummary("", (), (), {}, 0, 0),
            turn=1,
        )

        self.assertEqual({item.subject for item in evidence}, {
            "mail.label_created",
            "calendar.event_created",
        })
        self.assertTrue(all(item.value is True for item in evidence))

    def test_google_docs_mcp_provider_compares_pre_and_post_content(self) -> None:
        reads = 0

        def call_tool(_endpoint: str, name: str, _arguments: dict[str, Any]):
            nonlocal reads
            if name == "search_docs":
                return {
                    "files": [{
                        "id": "doc-1",
                        "name": "AccessoryHub Wholesale Price List — Q3 2026",
                    }]
                }
            if name == "docs.documents.get":
                reads += 1
                return {"documentId": "doc-1", "body": "before" if reads == 1 else "after"}
            raise AssertionError(name)

        contract = realreplica_contract_builder().build(
            "docs",
            'The document is titled **"AccessoryHub Wholesale Price List — Q3 2026"**; update it.',
        )
        provider = GoogleDocsMcpChangeObservationProvider(
            "http://127.0.0.1:3081/mcp",
            call_tool=call_tool,
        )
        provider.before_run(contract)
        evidence = provider.observe(
            contract,
            DeerFlowReplaySummary("", (), (), {}, 0, 0),
            turn=1,
        )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].subject, "document.updated")
        self.assertTrue(evidence[0].value)

    def test_output_file_count_provider_ignores_hidden_harness_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "outputs"
            outputs.mkdir()
            for name in ("one.json", "two.md", "three.csv"):
                (outputs / name).write_text(name)
            hidden = outputs / ".browser-frames"
            hidden.mkdir()
            (hidden / "frame.jpg").write_bytes(b"frame")
            contract = RuleBasedTaskContractBuilder().build(
                "files",
                "Write exactly three files to `outputs/`: `one.json`, `two.md`, `three.csv`.",
            )
            provider = OutputFileCountObservationProvider(root)

            evidence = provider.observe(
                contract,
                DeerFlowReplaySummary("", (), (), {}, 0, 0),
                turn=1,
            )

        self.assertEqual(evidence[0].subject, "files")
        self.assertEqual(evidence[0].value, 3)

    def test_workbench_calendar_provider_reads_public_materialized_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "outputs/mock_state/workbench_final.json"
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps(
                    {
                        "created_events": [
                            {"title": "Solar Pump Supplier RFQ Alignment"}
                        ]
                    }
                )
            )
            contract = realreplica_contract_builder().build(
                "workbench",
                "不要删除已有的日历事件；在日历里创建会议事件；"
                "标题里要带 `Solar Pump` 或 `RFQ`。",
            )
            provider = WorkbenchCalendarObservationProvider(root)

            evidence = provider.observe(
                contract,
                DeerFlowReplaySummary("", (), (), {}, 0, 0),
                turn=1,
            )

        self.assertEqual(len(evidence), 1)
        self.assertTrue(evidence[0].value)
        self.assertEqual(
            evidence[0].metadata["provider"],
            "workbench-materialized-state",
        )


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
    def test_policy_bridge_records_and_escalates_no_progress_resource_events(self) -> None:
        bridge = DeerFlowPolicyBridge(resource_guardrail=ResourceGuardrail())
        ledger = SessionLedger("resource-guardrail")
        progress = ProgressResult(
            ProgressStatus.NO_PROGRESS,
            (),
            (),
            (),
            "before",
            "after",
            "no task facts changed",
        )
        dispositions = []
        for turn in range(1, 4):
            ledger.append(
                "tool/action-audited",
                {
                    "record": {
                        "scope_key": "same-scope",
                        "argument_fingerprint": "same-strategy",
                        "mutation_epoch": 0,
                        "intent": "read",
                    }
                },
                turn=turn,
            )
            dispositions.append(
                bridge.check_resources(ledger, progress, turn=turn)[0]["disposition"]
            )

        self.assertEqual(dispositions, ["record", "replan", "block_scope"])
        state = TaskStateProjector().project(ledger.events)
        self.assertEqual(state.values["resource.no_progress_streak"], 3)
        self.assertTrue(state.values["resource.blocked_scope"])

    async def test_runtime_snapshots_request_and_cleans_environment(self) -> None:
        environment = _FakeEnvironment()
        client = _FakeClient()
        adapter = DeerFlowRuntimeAdapter(client, environment)
        request = DeerFlowRunRequest(
            message="finish task",
            thread_id="thread-1",
            client_options={"model_name": PRIMARY_MODEL},
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
            contract_builder=realreplica_contract_builder(),
            observation_providers=(StructuredDeerFlowObservationProvider(),),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
            progress_detector=RuleBasedProgressDetector(),
            recovery_outcome_evaluator=RuleBasedRecoveryOutcomeEvaluator(),
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
        outcomes = TaskStateProjector().project(result.ledger.events).recovery_outcomes
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].effective)

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
            policy_bridge=DeerFlowPolicyBridge(
                contract_builder=realreplica_contract_builder(),
                observation_providers=(provider,),
            ),
        ).run(
            DeerFlowRunRequest("帮我把商品发上线，发品系统打开后提交。", "thread-http"),
            run_id="run-http",
        )

        self.assertTrue(result.completed)
        evidence = next(event for event in result.ledger.events if event.type == "evidence/added")
        self.assertEqual(evidence.payload["evidence"]["subject"], "listing.submitted")
        self.assertTrue(evidence.payload["evidence"]["value"])

    async def test_unsupported_state_criteria_are_observe_only_in_candidate_mode(self) -> None:
        client = _TurnClient(
            [[
                _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                _RawEvent("end", {"usage": {"total_tokens": 5}}),
            ]]
        )
        bridge = DeerFlowPolicyBridge(
            contract_builder=realreplica_contract_builder(),
            observation_providers=(StructuredDeerFlowObservationProvider(),),
            unsupported_criteria="observe_only",
        )
        result = await DeerFlowRuntimeAdapter(
            client,
            _FakeEnvironment(),
            policy_bridge=bridge,
        ).run(
            DeerFlowRunRequest(
                "创建顶层标签，保存一封未发送草稿，再创建一个日历事件。",
                "thread-observe-only",
            ),
            run_id="run-observe-only",
        )

        policy = next(event for event in result.ledger.events if event.type == "policy/configured")
        self.assertTrue(result.completed)
        self.assertEqual(result.turns, 1)
        self.assertEqual(policy.payload["enforced_criterion_ids"], [])
        self.assertEqual(len(policy.payload["observe_only_criterion_ids"]), 3)

    async def test_mixed_contract_enforces_artifact_but_not_unprovided_count(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/report.json"
            output.parent.mkdir()
            output.write_text("{}")
            client = _TurnClient(
                [[
                    _RawEvent("messages-tuple", {"type": "ai", "id": "a1", "content": "done"}),
                    _RawEvent("end", {"usage": {"total_tokens": 5}}),
                ]]
            )
            bridge = DeerFlowPolicyBridge(
                observation_providers=(FileArtifactObservationProvider(root),),
                unsupported_criteria="observe_only",
            )
            result = await DeerFlowRuntimeAdapter(
                client,
                _FakeEnvironment(),
                policy_bridge=bridge,
            ).run(
                DeerFlowRunRequest(
                    "Write outputs/report.json with exactly 3 records.",
                    "thread-mixed",
                ),
                run_id="run-mixed",
            )

        policy = next(event for event in result.ledger.events if event.type == "policy/configured")
        self.assertTrue(result.completed)
        self.assertEqual(len(policy.payload["enforced_criterion_ids"]), 1)
        self.assertEqual(len(policy.payload["observe_only_criterion_ids"]), 1)

    async def test_recovery_decisions_are_ledger_backed_and_stop_when_exhausted(self) -> None:
        turns = [
            [
                _RawEvent(
                    "messages-tuple",
                    {"type": "ai", "id": f"a{index}", "content": "done"},
                ),
                _RawEvent("end", {"usage": {"total_tokens": 5}}),
            ]
            for index in (1, 2, 3)
        ]
        client = _TurnClient(turns)
        bridge = DeerFlowPolicyBridge(
            observation_providers=(FileArtifactObservationProvider(Path("/tmp")),),
            recovery_policy=RuleBasedTaskRecoveryPolicy(),
            recovery_executor=RuleBasedTaskRecoveryExecutor(),
            progress_detector=RuleBasedProgressDetector(),
            recovery_outcome_evaluator=RuleBasedRecoveryOutcomeEvaluator(),
            max_completion_turns=3,
        )
        result = await DeerFlowRuntimeAdapter(
            client,
            _FakeEnvironment(),
            policy_bridge=bridge,
        ).run(
            DeerFlowRunRequest("Write outputs/never-created.csv.", "thread-recovery"),
            run_id="run-recovery",
        )

        state = TaskStateProjector().project(result.ledger.events)
        headers = [event for event in result.ledger.events if event.type == "request/header"]
        progress = [event for event in result.ledger.events if event.type == "progress/checked"]
        self.assertFalse(result.completed)
        self.assertEqual(result.turns, 2)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(state.recoveries), 2)
        self.assertEqual(len(state.recovery_executions), 2)
        self.assertEqual(len(state.recovery_outcomes), 1)
        self.assertFalse(state.recovery_outcomes[0].effective)
        self.assertEqual(
            [event.payload["status"] for event in progress],
            ["progressed", "no_progress"],
        )
        self.assertEqual(
            state.recoveries[0].decision.actions,
            (
                TaskRecoveryAction.VALIDATE_CONTRACT,
                TaskRecoveryAction.WRITE_PARTIAL,
            ),
        )
        self.assertEqual(
            state.recoveries[1].decision.actions,
            (TaskRecoveryAction.STOP,),
        )
        self.assertIn("recent_recoveries", str(headers[1].payload["context"]["task"]))
        self.assertIn(
            "recovery.missing_requirements",
            headers[1].payload["context"]["task"]["values"],
        )
        self.assertTrue(
            state.values["recovery.stop_requested"],
        )
        self.assertEqual(state.values["progress.last_status"], "no_progress")


if __name__ == "__main__":
    unittest.main()
