#!/usr/bin/env python3
"""Zero-model MiniBench16 dataset, contract, history, and pairing preflight."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from adaptive_harness.integrations.realreplica import (
    RealReplicaMiniBenchAdapter,
    VariantSpec,
    stable_profile_fingerprint,
)

DEFAULT_IMAGE = "realreplicabench/deerflow:0debff98c1caf4a7d3047e8ef162d85a841b5c6d"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy-bridge-ready", action="store_true")
    args = parser.parse_args()

    adapter = RealReplicaMiniBenchAdapter()
    dataset = adapter.load(args.realreplica_root)
    coverage = adapter.contract_coverage(dataset)
    baseline = VariantSpec(
        "vanilla_deerflow",
        stable_profile_fingerprint({"runtime": "deerflow", "policies": []}),
        "deepseek-v4-flash",
        DEFAULT_IMAGE,
        dataset.seed,
    )
    candidate = VariantSpec(
        "adaptive_harness_a3",
        stable_profile_fingerprint(
            {
                "runtime": "deerflow",
                "policies": ["task_contract", "tool_reliability", "evidence_completion"],
                "tool_reliability": {"max_attempts": 2},
            }
        ),
        baseline.model,
        baseline.runtime_image,
        baseline.seed,
    )
    manifest = adapter.paired_manifest(dataset, baseline, candidate)
    history = adapter.historical_baselines(args.realreplica_root, dataset, baseline)
    history_rows = list(history.values())
    criterion_kinds = Counter(
        kind
        for row in coverage.rows
        for kind in row.criterion_kinds
    )
    history_config_hashes = sorted(
        {row.run_config_sha256 for row in history_rows if row.run_config_sha256}
    )
    gates = {
        "frozen_dataset_valid": len(dataset.tasks) == 16,
        "historical_baseline_all_tasks": len(history) == len(dataset.tasks),
        "paired_controls_valid": len(manifest.cells) == len(dataset.tasks) * 2,
        "contract_coverage_complete": coverage.covered_count == coverage.total_count,
        "live_policy_bridge_ready": args.policy_bridge_ready,
    }
    report = {
        "scope": "A4 MiniBench16 zero-model preflight",
        "model_calls": 0,
        "new_model_tokens": 0,
        "dataset": {
            "id": dataset.dataset_id,
            "fingerprint": dataset.fingerprint,
            "seed": dataset.seed,
            "counts": dataset.counts(),
            "blocks": {
                str(block): {
                    "purpose": purpose,
                    "task_count": sum(task.block == block for task in dataset.tasks),
                }
                for block, purpose in sorted(dataset.block_purposes.items())
            },
        },
        "contract_coverage": {
            "covered_tasks": coverage.covered_count,
            "total_tasks": coverage.total_count,
            "coverage_rate": coverage.covered_count / coverage.total_count,
            "criterion_count": sum(row.criterion_count for row in coverage.rows),
            "criterion_kinds": dict(sorted(criterion_kinds.items())),
            "uncovered_task_ids": list(coverage.uncovered_task_ids),
            "rows": [
                {
                    "task_id": row.task_id,
                    "block": row.block,
                    "criterion_count": row.criterion_count,
                    "criterion_kinds": list(row.criterion_kinds),
                }
                for row in coverage.rows
            ],
        },
        "historical_baseline": {
            "covered_tasks": len(history),
            "passed_tasks": sum(row.passed for row in history_rows),
            "failed_tasks": sum(not row.passed for row in history_rows),
            "known_total_tokens": sum(row.total_tokens or 0 for row in history_rows),
            "missing_token_usage_tasks": sum(row.total_tokens is None for row in history_rows),
            "run_config_fingerprint_count": len(history_config_hashes),
            "paired_reuse_allowed": False,
            "reason": "Historical runs cover preflight only; fresh baseline/candidate pairs are required.",
        },
        "paired_manifest": manifest.to_payload(),
        "gates": gates,
        "offline_preflight_passed": all(
            gates[key]
            for key in (
                "frozen_dataset_valid",
                "historical_baseline_all_tasks",
                "paired_controls_valid",
            )
        ),
        "paid_run_ready": all(gates.values()),
        "next_action": (
            "Run paired MiniBench blocks."
            if all(gates.values())
            else "Close contract coverage and live DeerFlow policy-bridge gaps before spending tokens."
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["offline_preflight_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
