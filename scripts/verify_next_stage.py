#!/usr/bin/env python3
"""Run the next-stage zero-model gates and report paid-run blockers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from preflight_minibench16 import _bridge_evidence_valid, _variant_specs

from adaptive_harness.integrations.minibench_gate import (
    PaidCanaryEvidence,
    decide_paid_canary,
    evaluate_partition_integrity,
)
from adaptive_harness.integrations.realreplica import (
    EvaluationRole,
    RealReplicaMiniBenchAdapter,
)

_ZERO_MODEL_TESTS = (
    "tests.test_runtime_conformance",
    "tests.test_policy_session",
    "tests.test_policy_profile",
    "tests.test_ledger_rollout",
    "tests.test_distiller_adapter",
    "tests.test_offline_evolution_e2e",
    "tests.test_minibench_gate",
    "tests.test_evaluation_adapter",
    "tests.test_architecture_boundaries",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-bridge-evidence", type=Path)
    args = parser.parse_args()

    test_command = [sys.executable, "-m", "unittest", *_ZERO_MODEL_TESTS]
    test_run = subprocess.run(test_command, capture_output=True, text=True, check=False)
    test_count_match = re.search(r"Ran (\d+) tests?", test_run.stderr)
    test_count = int(test_count_match.group(1)) if test_count_match else None

    adapter = RealReplicaMiniBenchAdapter()
    dataset = adapter.load(args.realreplica_root)
    partition = evaluate_partition_integrity(dataset)
    baseline, candidate = _variant_specs(dataset.seed)
    development_tasks = dataset.tasks_for_role(EvaluationRole.DEVELOPMENT)
    history = adapter.historical_baselines(args.realreplica_root, dataset, baseline)
    development_history_available = all(task.task_id in history for task in development_tasks)
    wiring = _bridge_evidence_valid(
        args.policy_bridge_evidence,
        dataset_fingerprint=dataset.fingerprint,
        candidate_profile_fingerprint=candidate.profile_fingerprint,
    )
    profile_reproducible = candidate.profile_fingerprint == _variant_specs(dataset.seed)[1].profile_fingerprint
    runtime_conformance_passed = test_run.returncode == 0
    adaptive_development_config = (
        args.realreplica_root / "configs/realreplicabench_adaptive_minibench16.yaml"
    ).read_text(encoding="utf-8")
    baseline_development_config = (
        args.realreplica_root / "configs/realreplicabench_deerflow_minibench16.yaml"
    ).read_text(encoding="utf-8")
    single_canary_default = all(
        re.search(r"^limit:\s*1\s*$", text, re.MULTILINE)
        and "minibench16.development.collection.json" in text
        for text in (adaptive_development_config, baseline_development_config)
    )

    # Stable controls must be rerun for this exact Profile after the new model,
    # PolicySession, and dataset fingerprint are pinned. Historical evidence is
    # intentionally not reinterpreted as current evidence.
    stable_controls_passed = False
    candidate_evidence = PaidCanaryEvidence(
        partition_integrity_passed=partition.passed,
        runtime_conformance_passed=runtime_conformance_passed,
        container_wiring_passed=wiring,
        historical_baseline_available=development_history_available,
        stable_regression_controls_passed=stable_controls_passed,
        profile_reproducible=profile_reproducible,
        credentials_external=True,
        single_canary_default=single_canary_default,
    )
    candidate_decision = decide_paid_canary(candidate_evidence)
    baseline_bootstrap_allowed = all(
        (
            partition.passed,
            runtime_conformance_passed,
            wiring,
            profile_reproducible,
            single_canary_default,
        )
    )
    zero_model_passed = all(
        (partition.passed, runtime_conformance_passed, profile_reproducible)
    )
    report = {
        "scope": "next-stage Runtime Conformance and Offline Evolution zero-model gate",
        "model_calls": 0,
        "new_model_tokens": 0,
        "dataset": {
            "id": dataset.dataset_id,
            "fingerprint": dataset.fingerprint,
            "counts": dataset.counts(),
            "partition_integrity": partition.to_payload(),
        },
        "profiles": {
            "baseline": baseline.to_payload(),
            "candidate": candidate.to_payload(),
            "candidate_reproducible": profile_reproducible,
        },
        "runtime_conformance": {
            "passed": runtime_conformance_passed,
            "command": test_command,
            "test_modules": list(_ZERO_MODEL_TESTS),
            "returncode": test_run.returncode,
            "test_count": test_count,
            "stdout_tail": test_run.stdout[-2000:],
            "stderr_tail": test_run.stderr[-2000:],
        },
        "container_wiring": {
            "passed": wiring,
            "evidence": str(args.policy_bridge_evidence) if args.policy_bridge_evidence else None,
        },
        "historical_baseline": {
            "development_required": len(development_tasks),
            "development_available": sum(task.task_id in history for task in development_tasks),
            "complete": development_history_available,
        },
        "stable_regression_controls": {
            "passed": stable_controls_passed,
            "reason": "fresh controls for the current model/Profile fingerprint do not exist",
        },
        "single_canary_default": single_canary_default,
        "zero_model_passed": zero_model_passed,
        "baseline_bootstrap_allowed": baseline_bootstrap_allowed,
        "paid_candidate_canary": candidate_decision.to_payload(),
        "next_action": (
            "Paid candidate canary is eligible."
            if candidate_decision.allowed
            else "Keep paid candidate disabled; refresh wiring, baseline, and stable controls in order."
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if zero_model_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
