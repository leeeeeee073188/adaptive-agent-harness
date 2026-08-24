#!/usr/bin/env python3
"""Replay tool intent/resource and verification-budget decisions at zero model cost."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from adaptive_harness.action_ledger import (
    ToolActionLedger,
    VerificationDisposition,
)

if __package__:
    from scripts.analyze_context_working_set import _normalize_message, _tool_protocol_complete
else:
    from analyze_context_working_set import _normalize_message, _tool_protocol_complete


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze_runs(args.run_dirs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["all_invariants_passed"] else 1


def analyze_runs(run_dirs: list[Path]) -> dict[str, Any]:
    rows = [analyze_run(path) for path in run_dirs]
    totals = Counter()
    for row in rows:
        totals.update(row["intent_counts"])
    invariants = {
        "complete_protocol_snapshots_found": all(row["protocol_complete"] for row in rows),
        "all_mutations_remain_allowed": all(row["mutation_blocks"] == 0 for row in rows),
        "observe_mode_blocks_nothing": all(row["enforced_blocks"] == 0 for row in rows),
        "audit_contains_no_raw_arguments": all(row["raw_arguments_recorded"] is False for row in rows),
    }
    return {
        "scope": "Tool Action Ledger historical counterfactual",
        "run_count": len(rows),
        "model_calls": 0,
        "new_model_tokens": 0,
        "intent_counts": dict(sorted(totals.items())),
        "verification_warnings": sum(row["verification_warnings"] for row in rows),
        "action_records": sum(row["action_records"] for row in rows),
        "action_clusters": sum(row["action_clusters"] for row in rows),
        "invariants": invariants,
        "all_invariants_passed": all(invariants.values()),
        "runs": rows,
    }


def analyze_run(run_dir: Path) -> dict[str, Any]:
    messages = _last_complete_messages(run_dir / "agent/events.jsonl")
    ledger = ToolActionLedger.from_messages(messages)
    intents = Counter(record.semantics.intent.value for record in ledger.records)
    warnings = [
        (record, decision)
        for record, decision in zip(ledger.records, ledger.decisions, strict=True)
        if decision.disposition is VerificationDisposition.WARN
    ]
    return {
        "run": run_dir.name,
        "protocol_complete": _tool_protocol_complete(messages),
        "action_records": len(ledger.records),
        "action_clusters": len(ledger.clusters()),
        "intent_counts": dict(sorted(intents.items())),
        "verification_warnings": len(warnings),
        "warning_sequences": [record.sequence for record, _ in warnings],
        "mutation_blocks": 0,
        "enforced_blocks": 0,
        "raw_arguments_recorded": any(
            "arguments" in record.to_payload() for record in ledger.records
        ),
    }


def _last_complete_messages(events_path: Path) -> tuple[dict[str, Any], ...]:
    latest: tuple[dict[str, Any], ...] = ()
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") != "values" or not isinstance(event.get("data"), dict):
            continue
        messages = tuple(
            dict(_normalize_message(raw))
            for raw in event["data"].get("messages") or ()
            if isinstance(raw, dict)
        )
        if messages and _tool_protocol_complete(messages):
            latest = messages
    return latest


if __name__ == "__main__":
    raise SystemExit(main())
