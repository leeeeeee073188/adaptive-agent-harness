#!/usr/bin/env python3
"""Replay a historical RealReplicaBench DeerFlow run without calling a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from adaptive_harness.integrations.deerflow import DeerFlowEventAdapter
from adaptive_harness.ledger import SessionLedger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="RealReplicaBench task run directory")
    parser.add_argument("--output", type=Path, help="write the comparison report as JSON")
    parser.add_argument("--ledger", type=Path, help="persist canonical events as JSONL")
    args = parser.parse_args()

    agent_dir = args.run_dir / "agent"
    source_path, events = _load_events(agent_dir)
    trajectory = _read_json(agent_dir / "deerflow-trajectory.json")
    expected_usage = _read_json(agent_dir / "token_usage.json")

    ledger = SessionLedger(args.run_dir.name, args.ledger)
    summary = DeerFlowEventAdapter().replay(ledger, events)

    expected_response = str(trajectory.get("response_text") or "")
    expected_calls = trajectory.get("tool_calls") or []
    expected_results = trajectory.get("tool_results") or []
    checks = {
        "usage_exact": dict(summary.usage) == expected_usage,
        "tool_call_count": len(summary.tool_calls) == len(expected_calls),
        "tool_result_count": len(summary.tool_results) == len(expected_results),
        "response_exact": summary.response_text == expected_response,
    }
    report = {
        "run": args.run_dir.name,
        "source": str(source_path),
        "source_event_count": summary.source_event_count,
        "canonical_event_count": summary.canonical_event_count,
        "canonical_event_sha256": _canonical_hash(ledger),
        "actual": {
            "usage": dict(summary.usage),
            "tool_call_count": len(summary.tool_calls),
            "tool_result_count": len(summary.tool_results),
            "response": _text_identity(summary.response_text),
        },
        "expected": {
            "usage": expected_usage,
            "tool_call_count": len(expected_calls),
            "tool_result_count": len(expected_results),
            "response": _text_identity(expected_response),
        },
        "checks": checks,
        "passed": all(checks.values()),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


def _load_events(agent_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    json_path = agent_dir / "deerflow-events.json"
    if json_path.exists():
        document = _read_json(json_path)
        raw_events = document.get("events") if isinstance(document, dict) else document
        if not isinstance(raw_events, list):
            raise ValueError(f"invalid event collection: {json_path}")
        return json_path, [event for event in raw_events if isinstance(event, dict)]

    jsonl_path = agent_dir / "events.jsonl"
    events = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return jsonl_path, [event for event in events if isinstance(event, dict)]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_hash(ledger: SessionLedger) -> str:
    facts = [
        {"seq": event.seq, "type": event.type, "payload": event.payload}
        for event in ledger.events
    ]
    encoded = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _text_identity(text: str) -> dict[str, Any]:
    return {
        "length": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
