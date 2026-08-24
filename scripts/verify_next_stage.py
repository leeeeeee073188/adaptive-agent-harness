#!/usr/bin/env python3
"""Run the next-stage zero-model gates and report paid-run blockers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.preflight_minibench16 import _bridge_evidence_valid, _variant_specs
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
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
    parser.add_argument("--candidate-gate-evidence", type=Path)
    parser.add_argument(
        "--mode",
        choices=("zero-model", "paid-canary"),
        default="zero-model",
    )
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
    canary_baseline_available = bool(
        development_tasks and development_tasks[0].task_id in history
    )
    wiring = _bridge_evidence_valid(
        args.policy_bridge_evidence,
        dataset_fingerprint=dataset.fingerprint,
        candidate_profile_fingerprint=candidate.profile_fingerprint,
    )
    candidate_gate = _candidate_gate_valid(
        args.candidate_gate_evidence,
        wiring_evidence=args.policy_bridge_evidence,
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
    stable_controls_passed = candidate_gate
    candidate_evidence = PaidCanaryEvidence(
        partition_integrity_passed=partition.passed,
        runtime_conformance_passed=runtime_conformance_passed,
        container_wiring_passed=wiring,
        historical_baseline_available=canary_baseline_available,
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
        "gate_mode": args.mode,
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
        "candidate_zero_model_gate": {
            "passed": candidate_gate,
            "evidence": (
                str(args.candidate_gate_evidence)
                if args.candidate_gate_evidence
                else None
            ),
        },
        "historical_baseline": {
            "development_required": len(development_tasks),
            "development_available": sum(task.task_id in history for task in development_tasks),
            "complete": development_history_available,
            "single_canary_available": canary_baseline_available,
        },
        "stable_regression_controls": {
            "passed": stable_controls_passed,
            "reason": (
                "Next-generation zero-model replay and regression controls passed."
                if stable_controls_passed
                else "Fresh zero-model replay controls for the current Profile do not exist."
            ),
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
    return _gate_exit_code(report, args.mode)


def _candidate_gate_valid(
    path: Path | None,
    *,
    wiring_evidence: Path | None,
) -> bool:
    if path is None or wiring_evidence is None:
        return False
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
        wiring = json.loads(wiring_evidence.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return bool(
        gate.get("passed") is True
        and gate.get("candidate_single_development_canary_allowed") is True
        and gate.get("paid_expansion_allowed") is False
        and gate.get("model_calls") == 0
        and gate.get("new_model_tokens") == 0
        and gate.get("adaptive_source_sha256") == wiring.get("adaptive_source_sha256")
        and gate.get("executable_policy_profile_fingerprint")
        == wiring.get("executable_policy_profile_fingerprint")
    )


def _gate_exit_code(report: dict[str, object], mode: str) -> int:
    if mode == "zero-model":
        return 0 if report.get("zero_model_passed") is True else 1
    if mode == "paid-canary":
        decision = report.get("paid_candidate_canary")
        return 0 if isinstance(decision, dict) and decision.get("allowed") is True else 1
    raise ValueError(f"unknown gate mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
