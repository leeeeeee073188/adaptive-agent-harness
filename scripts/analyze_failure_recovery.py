#!/usr/bin/env python3
"""Map human-reviewed failures to bounded recovery decisions without a model."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryPolicy,
    TaskFailureCategory,
    TaskFailureContext,
    TaskRecoveryAction,
    parse_failure_categories,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labeled_dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = analyze(args.labeled_dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["policy_mapping_passed"] else 1


def analyze(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    policy = RuleBasedTaskRecoveryPolicy()
    results = []
    action_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    expected_hits = 0
    target_slice_hits = 0
    no_progress_signal_hits = 0
    no_progress_labels = 0
    for row in rows:
        primary = _category(row.get("primary_category"))
        secondary = parse_failure_categories(row.get("secondary_categories") or ())
        trajectory_path = _trajectory_path(row)
        trajectory = _read_json(trajectory_path)
        signals = _trajectory_signals(trajectory)
        context = TaskFailureContext(
            primary,
            secondary,
            repeated_action_count=signals["max_identical_signature_count"],
            tool_error_count=signals["tool_error_count"],
        )
        decision = policy.decide(context)
        actions = {action for action in decision.actions}
        expected = _expected_actions(primary)
        expected_hit = bool(actions & expected)
        expected_hits += expected_hit
        target_slice = bool(
            context.categories
            & {TaskFailureCategory.CONSTRAINT_MISS, TaskFailureCategory.ARTIFACT_ERROR}
        )
        target_hit = not target_slice or TaskRecoveryAction.VALIDATE_CONTRACT in actions
        target_slice_hits += bool(target_slice and target_hit)
        if TaskFailureCategory.NO_PROGRESS in context.categories:
            no_progress_labels += 1
            no_progress_signal_hits += bool(
                signals["max_identical_signature_count"] >= 2
                or signals["fallback_shell_count"] >= 3
            )
        primary_counts[primary.value] += 1
        action_counts.update(action.value for action in decision.actions)
        results.append(
            {
                "task_id": row["task_id"],
                "primary": primary.value,
                "secondary": [item.value for item in secondary],
                "actions": [action.value for action in decision.actions],
                "should_continue": decision.should_continue,
                "expected_action_hit": expected_hit,
                "target_slice": target_slice,
                "target_slice_hit": target_hit,
                "signals": signals,
            }
        )
    target_count = sum(result["target_slice"] for result in results)
    unsafe_retry_count = action_counts.get("retry", 0)
    return {
        "scope": "Dev20 human-labeled failure recovery simulation",
        "model_calls": 0,
        "new_model_tokens": 0,
        "failure_count": len(results),
        "primary_categories": dict(sorted(primary_counts.items())),
        "recommended_actions": dict(sorted(action_counts.items())),
        "category_action_coverage": expected_hits / len(results) if results else 0,
        "target_constraint_artifact_count": target_count,
        "target_constraint_artifact_coverage": (
            target_slice_hits / target_count if target_count else 0
        ),
        "no_progress_label_count": no_progress_labels,
        "no_progress_trajectory_signal_rate": (
            no_progress_signal_hits / no_progress_labels if no_progress_labels else None
        ),
        "unsafe_blind_retry_count": unsafe_retry_count,
        "bounded_recovery_count": sum(result["should_continue"] for result in results),
        "policy_mapping_passed": bool(
            results
            and expected_hits == len(results)
            and target_slice_hits == target_count
            and unsafe_retry_count == 0
        ),
        "interpretation": (
            "This validates recommendation mapping, not that a recovery would make the task pass."
        ),
        "rows": results,
    }


def _trajectory_signals(trajectory: dict[str, Any]) -> dict[str, int | bool]:
    calls = trajectory.get("tool_calls") or []
    results = trajectory.get("tool_results") or []
    signatures = Counter(
        json.dumps(
            {"name": call.get("name"), "args": call.get("args") or {}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for call in calls
        if isinstance(call, dict)
    )
    error_markers = ("error", "failed", "timeout", "not found", "invalid")
    tool_errors = sum(
        any(marker in str(result.get("content") or "").lower() for marker in error_markers)
        for result in results
        if isinstance(result, dict)
    )
    names = [str(call.get("name") or "") for call in calls if isinstance(call, dict)]
    response = str(trajectory.get("response_text") or "")
    return {
        "tool_call_count": len(calls),
        "unpaired_tool_call_count": max(0, len(calls) - len(results)),
        "max_identical_signature_count": max(signatures.values(), default=0),
        "fallback_shell_count": sum(name == "bash" for name in names),
        "browser_tool_count": sum(name.startswith("browser_") for name in names),
        "tool_error_count": tool_errors,
        "token_capped": "token budget exceeded" in response.lower(),
    }


def _expected_actions(primary: TaskFailureCategory) -> set[TaskRecoveryAction]:
    return {
        TaskFailureCategory.BROWSER_GROUNDING: {
            TaskRecoveryAction.REFRESH_STATE,
            TaskRecoveryAction.SWITCH_TOOL,
        },
        TaskFailureCategory.CONSTRAINT_MISS: {TaskRecoveryAction.VALIDATE_CONTRACT},
        TaskFailureCategory.WRONG_TOOL: {TaskRecoveryAction.SWITCH_TOOL},
        TaskFailureCategory.PLAN_INCOMPLETE: {TaskRecoveryAction.REPLAN},
        TaskFailureCategory.ARTIFACT_ERROR: {TaskRecoveryAction.VALIDATE_CONTRACT},
    }.get(primary, {TaskRecoveryAction.STOP})


def _trajectory_path(row: dict[str, Any]) -> Path:
    candidates = [
        Path(value)
        for value in row.get("evidence_refs") or ()
        if str(value).endswith("/agent/trajectory.json")
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one trajectory ref for {row.get('task_id')}")
    return candidates[0]


def _category(value: Any) -> TaskFailureCategory:
    text = str(value or "UNKNOWN")
    return (
        TaskFailureCategory(text)
        if text in TaskFailureCategory._value2member_map_
        else TaskFailureCategory.UNKNOWN
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
