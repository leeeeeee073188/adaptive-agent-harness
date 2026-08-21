#!/usr/bin/env python3
"""Replay MiniBench historical terminal state through the provider-aware gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from preflight_minibench16 import _variant_specs

from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    FileArtifactObservationProvider,
    OutputFileCountObservationProvider,
)
from adaptive_harness.integrations.realreplica import RealReplicaMiniBenchAdapter
from adaptive_harness.integrations.realreplica_contract import realreplica_contract_builder
from adaptive_harness.integrations.realreplica_observations import WorkbenchCalendarObservationProvider
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.task_contract import CriterionKind, TaskContract
from adaptive_harness.task_state import Evidence, EvidenceKind, EvidenceSource


class HistoricalListingObservationProvider:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject") == "listing.submitted"
        )

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> tuple[Evidence, ...]:
        observed = bool(
            self.manifest.get("agent_early_terminated")
            and "status=submitted" in str(self.manifest.get("agent_early_terminate_reason"))
        )
        return (
            Evidence(
                f"historical:t{turn}:listing.submitted",
                EvidenceKind.OBSERVATION,
                "listing.submitted",
                observed,
                EvidenceSource.RUNTIME_OBSERVATION,
                {"provider": "historical-public-early-terminate"},
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = analyze(args.realreplica_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["safe_to_enable_provider_aware_gate"] else 1


def analyze(root: Path) -> dict[str, Any]:
    adapter = RealReplicaMiniBenchAdapter()
    dataset = adapter.load(root)
    baseline, _ = _variant_specs(dataset.seed)
    history = adapter.historical_baselines(root, dataset, baseline)
    rows = []
    empty_summary = DeerFlowReplaySummary("", (), (), {}, 0, 0)
    for task in dataset.tasks:
        historical = history[task.task_id]
        batch = _read_json(root / "runs" / historical.run_id / "summary.json")
        result = next(row for row in batch["results"] if row["task_id"] == task.task_id)
        run_dir = Path(result["run_dir"])
        manifest = _read_json(run_dir / "manifest.json")
        providers = (
            FileArtifactObservationProvider(run_dir / "workspace"),
            OutputFileCountObservationProvider(run_dir / "workspace"),
            WorkbenchCalendarObservationProvider(run_dir / "workspace"),
            HistoricalListingObservationProvider(manifest),
        )
        bridge = DeerFlowPolicyBridge(
            contract_builder=realreplica_contract_builder(),
            observation_providers=providers,
            unsupported_criteria="observe_only",
        )
        ledger = SessionLedger(f"counterfactual:{task.task_id}")
        contract = bridge.start(
            ledger,
            task_id=task.task_id,
            task_prompt=task.prompt,
            public_schema=None,
        )
        bridge.observe_turn(ledger, contract, empty_summary, turn=1)
        completion, _, _ = bridge.check_completion(ledger)
        would_block = not completion.passed
        actual_passed = historical.passed
        outcome = (
            "blocked_success"
            if would_block and actual_passed
            else "blocked_failure"
            if would_block
            else "accepted_success"
            if actual_passed
            else "accepted_failure_out_of_scope"
        )
        policy_event = next(event for event in ledger.events if event.type == "policy/configured")
        rows.append(
            {
                "task_id": task.task_id,
                "block": task.block,
                "historical_passed": actual_passed,
                "would_block_completion": would_block,
                "outcome": outcome,
                "enforced_criteria": len(policy_event.payload["enforced_criterion_ids"]),
                "observe_only_criteria": len(policy_event.payload["observe_only_criterion_ids"]),
                "missing_count": len(completion.missing),
            }
        )
    counts = {
        outcome: sum(row["outcome"] == outcome for row in rows)
        for outcome in (
            "blocked_failure",
            "blocked_success",
            "accepted_success",
            "accepted_failure_out_of_scope",
        )
    }
    return {
        "scope": "MiniBench16 provider-aware completion counterfactual",
        "model_calls": 0,
        "new_model_tokens": 0,
        "task_count": len(rows),
        "counts": counts,
        "failure_capture_rate": counts["blocked_failure"]
        / (counts["blocked_failure"] + counts["accepted_failure_out_of_scope"]),
        "successful_task_false_block_rate": counts["blocked_success"]
        / (counts["blocked_success"] + counts["accepted_success"]),
        "safe_to_enable_provider_aware_gate": counts["blocked_success"] == 0,
        "interpretation": (
            "Accepted failures may contain constraint/content errors outside the completion gate's scope."
        ),
        "rows": rows,
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
