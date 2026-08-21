#!/usr/bin/env python3
"""Scan real adaptive Ledgers and keep recovery Practice fail-closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_harness.ledger import SessionLedger
from adaptive_harness.recovery_practice import RecoveryOutcomeAggregator, RecoveryPracticeGate
from adaptive_harness.task_state import TaskStateProjector


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_runs", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ledgers = sorted(args.realreplica_runs.glob("*/tasks/*/agent/adaptive-ledger.jsonl"))
    outcomes = []
    replayed = 0
    for path in ledgers:
        try:
            state = TaskStateProjector().project(SessionLedger.replay(path).events)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        replayed += 1
        outcomes.extend(state.recovery_outcomes)
    stats = RecoveryOutcomeAggregator().aggregate(outcomes)
    gate = RecoveryPracticeGate()
    decisions = [gate.evaluate(row) for row in stats]
    eligible = [decision.action.value for decision in decisions if decision.eligible]
    report = {
        "scope": "Real recovery outcome Practice gate",
        "model_calls": 0,
        "new_model_tokens": 0,
        "ledger_files_found": len(ledgers),
        "ledger_files_replayed": replayed,
        "real_outcome_count": len(outcomes),
        "thresholds": {
            "min_isolated_samples": gate.min_isolated_samples,
            "min_effective_rate": gate.min_effective_rate,
            "min_wilson_lower": gate.min_wilson_lower,
        },
        "actions": [
            {
                "action": decision.action.value,
                "eligible": decision.eligible,
                "reason": decision.reason,
                "total_samples": decision.stats.total_samples,
                "isolated_samples": decision.stats.isolated_samples,
                "confounded_samples": decision.stats.confounded_samples,
                "isolated_effective": decision.stats.isolated_effective,
                "effective_rate": decision.stats.effective_rate,
                "wilson_lower": decision.stats.wilson_lower,
                "wilson_upper": decision.stats.wilson_upper,
            }
            for decision in decisions
        ],
        "eligible_actions": eligible,
        "practice_enabled": bool(eligible),
        "status": "eligible" if eligible else "insufficient_real_outcomes",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
