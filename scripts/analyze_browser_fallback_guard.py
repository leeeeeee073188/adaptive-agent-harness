#!/usr/bin/env python3
"""Replay a browser fallback guard candidate over the frozen Dev20 trajectories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from adaptive_harness.guardrails import BrowserFallbackGuard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dev20_summary", type=Path)
    parser.add_argument("--labeled-failures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-browser-bypass-calls", type=int, default=3)
    args = parser.parse_args()

    summary = _read_json(args.dev20_summary)
    labels = {
        row["task_id"]: row
        for row in (
            json.loads(line)
            for line in args.labeled_failures.read_text().splitlines()
            if line.strip()
        )
    }
    rows = []
    for row in summary.get("rows") or ():
        trajectory = _read_json(Path(row["run_dir"]) / "agent/trajectory.json")
        bypass_count = sum(
            BrowserFallbackGuard.is_browser_bypass(
                str(call.get("name") or ""),
                call.get("args") or {},
            )
            for call in trajectory.get("tool_calls") or ()
            if isinstance(call, dict)
        )
        blocked_calls = max(0, bypass_count - args.max_browser_bypass_calls)
        label = labels.get(row["task_id"]) or {}
        rows.append(
            {
                "task_id": row["task_id"],
                "category": row["category"],
                "passed": bool(row["passed"]),
                "primary_failure": label.get("primary_category"),
                "secondary_failures": label.get("secondary_categories") or [],
                "browser_bypass_calls": bypass_count,
                "would_block_calls": blocked_calls,
                "guard_activated": row["category"] == "browser" and blocked_calls > 0,
            }
        )
    browser_failures = [row for row in rows if row["category"] == "browser" and not row["passed"]]
    browser_successes = [row for row in rows if row["category"] == "browser" and row["passed"]]
    no_progress = [row for row in browser_failures if "NO_PROGRESS" in row["secondary_failures"]]
    covered_failures = sum(row["guard_activated"] for row in browser_failures)
    covered_no_progress = sum(row["guard_activated"] for row in no_progress)
    nonbrowser_blocks = sum(
        row["would_block_calls"]
        for row in rows
        if row["category"] != "browser"
    )
    deployment_ready = bool(
        browser_successes
        and all(not row["guard_activated"] for row in browser_successes)
        and nonbrowser_blocks == 0
    )
    report: dict[str, Any] = {
        "scope": "Dev20 browser shell/CDP fallback guard replay",
        "model_calls": 0,
        "new_model_tokens": 0,
        "max_browser_bypass_calls": args.max_browser_bypass_calls,
        "task_count": len(rows),
        "browser_failure_count": len(browser_failures),
        "browser_failure_signal_coverage": (
            covered_failures / len(browser_failures) if browser_failures else None
        ),
        "no_progress_browser_count": len(no_progress),
        "no_progress_signal_coverage": (
            covered_no_progress / len(no_progress) if no_progress else None
        ),
        "successful_browser_control_count": len(browser_successes),
        "nonbrowser_would_block_calls": nonbrowser_blocks,
        "total_would_block_calls": sum(row["would_block_calls"] for row in rows),
        "deployment_ready": deployment_ready,
        "candidate_enabled": False,
        "status": (
            "ready_for_canary" if deployment_ready else "insufficient_successful_browser_controls"
        ),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
