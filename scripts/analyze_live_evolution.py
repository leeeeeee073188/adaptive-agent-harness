#!/usr/bin/env python3
"""Summarize paid Development iterations without exposing verifier-private details."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = analyze_live_runs(args.run_dirs, args.task_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def analyze_live_runs(run_dirs: list[Path], task_id: str) -> dict[str, Any]:
    rows = []
    incomplete = []
    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        if not summary_path.is_file():
            incomplete.append({"run_dir": str(run_dir), "reason": "summary missing"})
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        matches = [result for result in summary.get("results") or () if result.get("task_id") == task_id]
        if len(matches) != 1:
            incomplete.append(
                {
                    "run_id": summary.get("run_id"),
                    "run_dir": str(run_dir),
                    "reason": "no completed task result",
                }
            )
            continue
        result = matches[0]
        task_dir = Path(result["run_dir"])
        ledger_path = task_dir / "agent/adaptive-ledger.jsonl"
        ledger = _read_jsonl(ledger_path)
        usage = result.get("usage") or {}
        row = {
            "run_id": summary.get("run_id"),
            "variant": (summary.get("experiment") or {}).get("variant"),
            "model": summary.get("model_name"),
            "passed": bool(result.get("passed")),
            "capacity_score": float(result.get("capacity_score") or 0.0),
            "checks_passed": int(result.get("checks_passed") or 0),
            "checks_total": int(result.get("checks_total") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "tool_calls": int(result.get("tool_call_count") or 0),
            "elapsed_sec": float(result.get("elapsed_sec") or 0.0),
            "output_file_count": int(result.get("output_file_count") or 0),
            "integrity_passed": result.get("integrity_passed") is True,
            "ledger_event_count": len(ledger),
            "resource": _resource_summary(ledger),
        }
        rows.append(row)

    if not rows:
        raise ValueError("no completed live results")
    baselines = [row for row in rows if row["variant"] == "vanilla_deerflow"]
    if len(baselines) != 1:
        raise ValueError(f"expected exactly one vanilla_deerflow baseline, found {len(baselines)}")
    baseline = baselines[0]
    candidates = [row for row in rows if row is not baseline]
    if not candidates:
        raise ValueError("expected at least one non-baseline candidate")
    best = max(
        candidates,
        key=lambda row: (
            row["passed"],
            row["capacity_score"],
            -row["total_tokens"],
            -row["elapsed_sec"],
        ),
    )
    for row in rows:
        row["delta_vs_baseline"] = {
            "capacity_score": row["capacity_score"] - baseline["capacity_score"],
            "total_tokens": row["total_tokens"] - baseline["total_tokens"],
            "token_fraction": _fraction(row["total_tokens"], baseline["total_tokens"]),
            "tool_calls": row["tool_calls"] - baseline["tool_calls"],
            "elapsed_sec": row["elapsed_sec"] - baseline["elapsed_sec"],
        }
        row["decision"] = "reference" if row is baseline else "keep_shadow"

    return {
        "scope": "paid single-task Development runtime evolution",
        "task_id": task_id,
        "full_107_run": False,
        "completed_run_count": len(rows),
        "incomplete_runs": incomplete,
        "observed_total_tokens": sum(row["total_tokens"] for row in rows),
        "baseline_run_id": baseline["run_id"],
        "selected_candidate_run_id": best["run_id"],
        "selected_candidate_variant": best["variant"],
        "selected_candidate_passed": best["passed"],
        "selected_candidate_decision": "keep_shadow",
        # A single Development task can select a Shadow for further offline work,
        # but cannot authorize more paid tasks or cross-partition execution.
        "paid_expansion_allowed": False,
        "selection_reason": ("highest public capacity score, then lower observed tokens and latency"),
        "runs": rows,
        "claim_boundary": _claim_boundary(best),
    }


def _claim_boundary(selected: dict[str, Any]) -> str:
    result = "passed this task" if selected["passed"] else "did not pass"
    return (
        "One Development task only. The selected candidate "
        f"{result}; it remains Shadow and cannot justify more paid tasks, Transfer, Held-out, "
        "or MiniBench-wide success-rate claims."
    )


def _resource_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [event for event in events if event.get("type") == "resource/no-progress-checked"]
    return {
        "check_count": len(checks),
        "dispositions": dict(Counter((event.get("payload") or {}).get("disposition") for event in checks)),
        "blocked_tool_results": sum(
            "blocked" in str(((event.get("payload") or {}).get("record") or {}).get("result_preview", "")).lower()
            for event in events
            if event.get("type") == "tool/action-audited"
        ),
    }


def _fraction(candidate: int, baseline: int) -> float | None:
    return (candidate - baseline) / baseline if baseline else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
