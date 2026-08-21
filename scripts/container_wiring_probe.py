#!/usr/bin/env python3
"""Executed inside the pinned DeerFlow image; never calls a model provider."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from deerflow.client import DeerFlowClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from adaptive_harness.integrations.deerflow import (
    DeerFlowRunRequest,
    DeerFlowRuntimeAdapter,
)
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    StructuredDeerFlowObservationProvider,
)


class ProbeEnvironment:
    def __init__(self) -> None:
        self.built = False
        self.cleaned = False

    async def build(self) -> None:
        self.built = True

    async def cleanup(self) -> None:
        self.cleaned = True

    def state(self) -> dict[str, object]:
        return {"probe": True, "network": "disabled"}

    async def tools(self) -> list[object]:
        return []


class DeterministicAgent:
    """LangGraph-shaped stream source with no BaseChatModel or network path."""

    def __init__(self) -> None:
        self.calls = 0
        self.ai1 = AIMessage(
            content="done",
            id="probe-ai-1",
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )

    def stream(self, state, **kwargs):
        self.calls += 1
        if self.calls == 1:
            messages = [HumanMessage(content="submit", id="probe-human-1"), self.ai1]
        else:
            evidence = {
                "adaptive_evidence": [
                    {
                        "kind": "observation",
                        "subject": "listing.submitted",
                        "value": True,
                    }
                ]
            }
            messages = [
                HumanMessage(content="submit", id="probe-human-1"),
                self.ai1,
                HumanMessage(content="evidence required", id="probe-human-2"),
                ToolMessage(
                    content="submitted",
                    tool_call_id="probe-call-1",
                    name="submit_listing",
                    id="probe-tool-1",
                    artifact=evidence,
                ),
                AIMessage(
                    content="done with evidence",
                    id="probe-ai-2",
                    usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                ),
            ]
        return iter([("values", {"title": None, "messages": messages, "artifacts": []})])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()

    environment = ProbeEnvironment()
    agent = DeterministicAgent()
    client = DeerFlowClient()
    bridge = DeerFlowPolicyBridge(
        observation_providers=(StructuredDeerFlowObservationProvider(),),
        max_completion_turns=2,
    )
    adapter = DeerFlowRuntimeAdapter(client, environment, policy_bridge=bridge)
    with (
        patch.object(client, "_ensure_agent", return_value=None),
        patch.object(client, "_agent", agent),
        patch("deerflow.client.build_tracing_callbacks", return_value=[]),
        patch("deerflow.client.is_trace_correlation_enabled", return_value=False),
        patch("deerflow.runtime.checkpointer.get_checkpointer", return_value=None),
    ):
        result = asyncio.run(
            adapter.run(
                DeerFlowRunRequest(
                    "帮我把商品发上线，发品系统打开后提交。",
                    "probe-thread",
                    task_id="public-probe",
                ),
                run_id="container-wiring-probe",
                ledger_path=args.ledger,
            )
        )

    event_types = [event.type for event in result.ledger.events]
    checks = {
        "adaptive_package_imported": True,
        "embedded_client_stream_exercised": agent.calls == 2,
        "ledger_persisted": args.ledger.is_file() and args.ledger.stat().st_size > 0,
        "policy_bridge_enabled": result.completed and result.turns == 2,
        "same_thread_usage_recorded": event_types.count("runtime/turn-end") == 2,
        "structured_evidence_recorded": event_types.count("evidence/added") == 1,
        "single_runtime_end": event_types.count("runtime/end") == 1,
        "environment_cleaned": environment.cleaned,
    }
    payload = {
        "passed": all(checks.values()),
        "checks": checks,
        "model_calls": 0,
        "agent_stream_calls": agent.calls,
        "ledger_event_count": len(result.ledger.events),
        "ledger_sha256": hashlib.sha256(args.ledger.read_bytes()).hexdigest(),
        "usage": dict(result.summary.usage),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
