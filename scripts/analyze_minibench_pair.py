#!/usr/bin/env python3
"""Evaluate one fresh MiniBench baseline/candidate pair and enforce stop gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_run", type=Path)
    parser.add_argument("candidate_run", type=Path)
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-token-increase", type=float, default=0.25)
    args = parser.parse_args()

    report = analyze_pair(
        args.baseline_run,
        args.candidate_run,
        args.task_id,
        max_token_increase=args.max_token_increase,
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
    max_token_increase: float,
) -> dict[str, Any]:
    baseline_summary = _read_json(baseline_run / "summary.json")
    candidate_summary = _read_json(candidate_run / "summary.json")
    baseline = _task_result(baseline_summary, task_id)
    candidate = _task_result(candidate_summary, task_id)
    baseline_dir = Path(baseline["run_dir"])
    candidate_dir = Path(candidate["run_dir"])
    candidate_integrity = _read_json(candidate_dir / "integrity.json")
    ledger_path = candidate_dir / "agent/adaptive-ledger.jsonl"
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines() if line.strip()]
    completion = [event for event in ledger if event.get("type") == "completion/checked"]
    evidence = [event for event in ledger if event.get("type") == "evidence/added"]
    baseline_tokens = int((baseline.get("usage") or {}).get("total_tokens") or 0)
    candidate_tokens = int((candidate.get("usage") or {}).get("total_tokens") or 0)
    token_delta = candidate_tokens - baseline_tokens
    token_increase = token_delta / baseline_tokens if baseline_tokens else None
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
        "token_cost_within_limit": token_increase is not None and token_increase <= max_token_increase,
    }
    pair_valid = all(value for key, value in gates.items() if key != "token_cost_within_limit")
    continue_block = pair_valid and gates["token_cost_within_limit"]
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
        },
        "controls": controls,
        "output_comparison": output_comparison,
        "cost": {
            "token_delta": token_delta,
            "token_increase_fraction": token_increase,
            "max_token_increase_fraction": max_token_increase,
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
            else "stop before remaining Block 1 tasks and optimize cost"
        ),
        "analysis_model_calls": 0,
        "analysis_new_tokens": 0,
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


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
