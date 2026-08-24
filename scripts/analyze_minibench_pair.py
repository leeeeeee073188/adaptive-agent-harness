#!/usr/bin/env python3
"""Evaluate one fresh MiniBench baseline/candidate pair and enforce stop gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from adaptive_harness.integrations.deerflow import DeerFlowEventAdapter
from adaptive_harness.ledger import SessionLedger


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_run", type=Path)
    parser.add_argument("candidate_run", type=Path)
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--token-diagnostic-threshold",
        "--max-token-increase",
        dest="token_diagnostic_threshold",
        type=float,
        default=0.25,
        help="report-only threshold; never rejects or stops a valid pair",
    )
    parser.add_argument("--history-root", type=Path)
    args = parser.parse_args()

    report = analyze_pair(
        args.baseline_run,
        args.candidate_run,
        args.task_id,
        token_diagnostic_threshold=args.token_diagnostic_threshold,
        history_root=args.history_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["pair_valid"] else 1


def analyze_pair(
    baseline_run: Path,
    candidate_run: Path,
    task_id: str,
    *,
    token_diagnostic_threshold: float,
    history_root: Path | None = None,
) -> dict[str, Any]:
    baseline_summary = _read_json(baseline_run / "summary.json")
    candidate_summary = _read_json(candidate_run / "summary.json")
    baseline = _task_result(baseline_summary, task_id)
    candidate = _task_result(candidate_summary, task_id)
    baseline_dir = Path(baseline["run_dir"])
    candidate_dir = Path(candidate["run_dir"])
    baseline_integrity = _read_json(baseline_dir / "integrity.json")
    candidate_integrity = _read_json(candidate_dir / "integrity.json")
    candidate_trajectory = _read_json(candidate_dir / "agent/deerflow-trajectory.json")
    ledger_path = candidate_dir / "agent/adaptive-ledger.jsonl"
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
    completion = [event for event in ledger if event.get("type") == "completion/checked"]
    evidence = [event for event in ledger if event.get("type") == "evidence/added"]
    resource = _resource_guardrail_summary(ledger)
    baseline_tokens = int((baseline.get("usage") or {}).get("total_tokens") or 0)
    candidate_tokens = int((candidate.get("usage") or {}).get("total_tokens") or 0)
    token_delta = candidate_tokens - baseline_tokens
    token_increase = token_delta / baseline_tokens if baseline_tokens else None
    candidate_turns = int((candidate_trajectory.get("adaptive") or {}).get("turns", 0) or 0)
    model_surface = _model_surface(baseline_summary, candidate_summary, baseline_integrity, candidate_integrity)
    single_turn_surface_equivalent = model_surface["equal"] and candidate_turns == 1
    attributable_token_delta = 0 if single_turn_surface_equivalent else token_delta
    attributable_increase = (
        attributable_token_delta / baseline_tokens if baseline_tokens else None
    )
    history = _historical_distribution(
        history_root,
        task_id,
        model=baseline_summary.get("model_name"),
        image=baseline_summary.get("image"),
        seed=(baseline_summary.get("experiment") or {}).get("seed"),
    )
    counterfactual = _counterfactual_projection(candidate_dir, candidate_trajectory)
    controls = {
        "task_id": baseline.get("task_id") == candidate.get("task_id") == task_id,
        "model": baseline_summary.get("model_name") == candidate_summary.get("model_name"),
        "runtime_image": baseline_summary.get("image") == candidate_summary.get("image"),
        "seed": (baseline_summary.get("experiment") or {}).get("seed")
        == (candidate_summary.get("experiment") or {}).get("seed"),
        "split": (baseline_summary.get("experiment") or {}).get("split")
        == (candidate_summary.get("experiment") or {}).get("split"),
    }
    output_comparison = _compare_outputs(
        baseline_dir / "workspace/outputs",
        candidate_dir / "workspace/outputs",
    )
    gates = {
        "paired_controls": all(controls.values()),
        "baseline_integrity": bool(baseline.get("integrity_passed")),
        "candidate_integrity": bool(candidate.get("integrity_passed"))
        and candidate_integrity.get("passed") is True,
        "quality_non_regression": bool(candidate.get("passed"))
        and float(candidate.get("capacity_score") or 0) >= float(baseline.get("capacity_score") or 0),
        "semantic_outputs_equal": output_comparison["semantic_equal"],
        "candidate_ledger_present": bool(ledger),
        "completion_evidence_passed": bool(completion)
        and completion[-1].get("payload", {}).get("passed") is True
        and bool(evidence),
        "no_uncontrolled_no_progress_loop": not resource["uncontrolled_no_progress_loop"],
    }
    pair_valid = all(gates.values())
    token_increase_over_diagnostic_threshold = bool(
        token_increase is not None and token_increase > token_diagnostic_threshold
    )
    architecture_increase_over_diagnostic_threshold = bool(
        attributable_increase is not None
        and attributable_increase > token_diagnostic_threshold
    )
    cost_attribution_confident = bool(
        not token_increase_over_diagnostic_threshold
        or (not single_turn_surface_equivalent)
        or (history and history["sample_count"] >= 6)
    )
    # Token deltas remain visible diagnostics. Productive exploration is not
    # rejected by a fixed growth ratio; no-progress/runaway policies are the
    # execution guardrail and are evaluated from the ledger separately.
    continue_block = pair_valid
    return {
        "scope": "MiniBench fresh paired canary",
        "task_id": task_id,
        "baseline": {
            "run_id": baseline_summary["run_id"],
            "variant": (baseline_summary.get("experiment") or {}).get("variant"),
            "passed": baseline.get("passed"),
            "capacity_score": baseline.get("capacity_score"),
            "total_tokens": baseline_tokens,
            "tool_calls": baseline.get("tool_call_count"),
            "elapsed_sec": baseline.get("elapsed_sec"),
        },
        "candidate": {
            "run_id": candidate_summary["run_id"],
            "variant": (candidate_summary.get("experiment") or {}).get("variant"),
            "passed": candidate.get("passed"),
            "capacity_score": candidate.get("capacity_score"),
            "total_tokens": candidate_tokens,
            "tool_calls": candidate.get("tool_call_count"),
            "elapsed_sec": candidate.get("elapsed_sec"),
            "ledger_event_count": len(ledger),
            "turns": candidate_turns,
        },
        "controls": controls,
        "model_visible_surface": model_surface,
        "counterfactual_projection": counterfactual,
        "historical_vanilla_distribution": history,
        "output_comparison": output_comparison,
        "resource_guardrail": resource,
        "cost": {
            "token_delta": token_delta,
            "token_increase_fraction": token_increase,
            "token_diagnostic_threshold_fraction": token_diagnostic_threshold,
            "token_increase_over_diagnostic_threshold": token_increase_over_diagnostic_threshold,
            "attributable_architecture_token_delta": attributable_token_delta,
            "attributable_architecture_increase_fraction": attributable_increase,
            "architecture_increase_over_diagnostic_threshold": (
                architecture_increase_over_diagnostic_threshold
            ),
            "attribution": (
                "provider_or_trajectory_variance"
                if single_turn_surface_equivalent
                else "candidate_policy_may_have_changed_model_work"
            ),
            "attribution_confident": cost_attribution_confident,
            "tool_call_delta": int(candidate.get("tool_call_count") or 0)
            - int(baseline.get("tool_call_count") or 0),
            "elapsed_delta_sec": float(candidate.get("elapsed_sec") or 0)
            - float(baseline.get("elapsed_sec") or 0),
        },
        "gates": gates,
        "pair_valid": pair_valid,
        "continue_block": continue_block,
        "decision": (
            "continue Block 1"
            if continue_block
            else "stop before remaining Block 1 tasks because a quality or integrity gate failed"
        ),
        "analysis_model_calls": 0,
        "analysis_new_tokens": 0,
    }


def _resource_guardrail_summary(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [
        event
        for event in ledger
        if event.get("type") == "resource/no-progress-checked"
    ]
    blocked = [
        event
        for event in checks
        if (event.get("payload") or {}).get("disposition") == "block_scope"
    ]
    uncontrolled = False
    for blocked_event in blocked:
        blocked_index = ledger.index(blocked_event)
        payload = blocked_event.get("payload") or {}
        signature = (payload.get("scope_key"), payload.get("strategy_fingerprint"))
        for later in ledger[blocked_index + 1 :]:
            if later.get("type") != "tool/action-audited":
                continue
            record = (later.get("payload") or {}).get("record") or {}
            if (record.get("scope_key"), record.get("argument_fingerprint")) == signature:
                uncontrolled = True
                break
        if uncontrolled:
            break
    return {
        "check_count": len(checks),
        "replan_count": sum(
            (event.get("payload") or {}).get("disposition") == "replan"
            for event in checks
        ),
        "block_scope_count": len(blocked),
        "uncontrolled_no_progress_loop": uncontrolled,
    }


def _task_result(summary: dict[str, Any], task_id: str) -> dict[str, Any]:
    matches = [row for row in summary.get("results") or () if row.get("task_id") == task_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one result for {task_id!r}")
    return matches[0]


def _compare_outputs(baseline: Path, candidate: Path) -> dict[str, Any]:
    baseline_files = _output_hashes(baseline)
    candidate_files = _output_hashes(candidate)
    return {
        "same_paths": set(baseline_files) == set(candidate_files),
        "semantic_equal": baseline_files == candidate_files,
        "file_count": len(candidate_files),
        "baseline_hashes": baseline_files,
        "candidate_hashes": candidate_files,
    }


def _output_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        if b"\0" not in content:
            content = content.replace(b"\r\n", b"\n")
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
    return hashes


def _model_surface(
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    baseline_integrity: dict[str, Any],
    candidate_integrity: dict[str, Any],
) -> dict[str, Any]:
    components = {
        "prompt_sha256": (
            baseline_integrity.get("prompt_sha256"),
            candidate_integrity.get("prompt_sha256"),
        ),
        "config_sha256": (
            baseline_integrity.get("config_sha256"),
            candidate_integrity.get("config_sha256"),
        ),
        "model": (baseline_summary.get("model_name"), candidate_summary.get("model_name")),
        "runtime_image": (baseline_summary.get("image"), candidate_summary.get("image")),
    }
    checks = {
        key: left is not None and left == right
        for key, (left, right) in components.items()
    }
    canonical = json.dumps(
        {key: values[0] for key, values in components.items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "equal": all(checks.values()),
        "checks": checks,
        "baseline_fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
        "candidate_fingerprint": (
            hashlib.sha256(canonical.encode()).hexdigest()
            if all(checks.values())
            else None
        ),
    }


def _historical_distribution(
    root: Path | None,
    task_id: str,
    *,
    model: Any,
    image: Any,
    seed: Any,
) -> dict[str, Any] | None:
    if root is None:
        return None
    values = []
    for path in root.glob("*/summary.json"):
        try:
            summary = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        experiment = summary.get("experiment") or {}
        if (
            summary.get("harness") != "deerflow"
            or summary.get("model_name") != model
            or summary.get("image") != image
            or experiment.get("seed") != seed
            or experiment.get("variant") != "vanilla_deerflow"
        ):
            continue
        for row in summary.get("results") or ():
            tokens = (row.get("usage") or {}).get("total_tokens")
            if (
                row.get("task_id") == task_id
                and row.get("formal_result_eligible") is True
                and row.get("integrity_passed") is True
                and isinstance(tokens, int)
            ):
                values.append(tokens)
    if not values:
        return None
    values.sort()
    mean = statistics.fmean(values)
    return {
        "sample_count": len(values),
        "tokens": values,
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "median": statistics.median(values),
        "sample_stdev": statistics.stdev(values) if len(values) > 1 else None,
        "coefficient_of_variation": (
            statistics.stdev(values) / mean if len(values) > 1 and mean else None
        ),
    }


def _counterfactual_projection(
    candidate_dir: Path,
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    path = candidate_dir / "agent/deerflow-events.json"
    if not path.is_file():
        return None
    document = _read_json(path)
    events = document.get("events") or []
    summary = DeerFlowEventAdapter().replay(SessionLedger("counterfactual"), events)
    checks = {
        "response": summary.response_text == str(expected.get("response_text") or ""),
        "tool_calls": len(summary.tool_calls) == len(expected.get("tool_calls") or ()),
        "tool_results": len(summary.tool_results) == len(expected.get("tool_results") or ()),
        "usage": dict(summary.usage) == dict(expected.get("usage") or {}),
    }
    return {
        "exact": all(checks.values()),
        "checks": checks,
        "source_event_count": len(events),
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
