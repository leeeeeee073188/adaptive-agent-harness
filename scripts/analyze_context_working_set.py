#!/usr/bin/env python3
"""Counterfactually compile historical message snapshots with zero model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from adaptive_harness.context import (
    ContextBudget,
    TaskAwareContextManager,
    estimate_message_tokens,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--budget", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = analyze_runs(args.run_dirs, budget_tokens=args.budget)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["all_invariants_passed"] else 1


def analyze_runs(run_dirs: Sequence[Path], *, budget_tokens: int) -> dict[str, Any]:
    rows = [analyze_run(run_dir, budget_tokens=budget_tokens) for run_dir in run_dirs]
    full_tokens = sum(row["full_message_surface_tokens"] for row in rows)
    selected_tokens = sum(row["selected_message_surface_tokens"] for row in rows)
    reduction = 1 - selected_tokens / full_tokens if full_tokens else None
    per_run_reductions = [
        float(row["estimated_surface_reduction_fraction"])
        for row in rows
        if row["estimated_surface_reduction_fraction"] is not None
    ]
    invariants = {
        "snapshots_found": bool(rows) and all(row["snapshot_count"] > 0 for row in rows),
        "immutable_task_preserved": all(row["immutable_task_preserved"] for row in rows),
        "tool_protocol_preserved": all(row["tool_protocol_preserved"] for row in rows),
        "budget_respected_or_explicit_overflow": all(
            row["budget_respected_or_explicit_overflow"] for row in rows
        ),
        "surface_not_expanded": selected_tokens <= full_tokens,
    }
    return {
        "scope": "Task-aware context historical counterfactual",
        "budget_tokens": budget_tokens,
        "run_count": len(rows),
        "snapshot_count": sum(row["snapshot_count"] for row in rows),
        "full_message_surface_tokens": full_tokens,
        "selected_message_surface_tokens": selected_tokens,
        "estimated_surface_reduction_fraction": reduction,
        "median_run_surface_reduction_fraction": (
            statistics.median(per_run_reductions) if per_run_reductions else None
        ),
        "estimator_boundary": (
            "Dependency-free message-surface estimate only; excludes tool schemas, provider cache behavior, "
            "model tokenizer differences, output tokens, and quality effects."
        ),
        "model_calls": 0,
        "new_model_tokens": 0,
        "invariants": invariants,
        "all_invariants_passed": all(invariants.values()),
        "runs": rows,
    }


def analyze_run(run_dir: Path, *, budget_tokens: int) -> dict[str, Any]:
    events_path = run_dir / "agent/events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    snapshots: list[tuple[Mapping[str, Any], ...]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("type") != "values" or not isinstance(event.get("data"), Mapping):
            continue
        raw_messages = event["data"].get("messages")
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            continue
        messages = tuple(
            _normalize_message(message)
            for message in raw_messages
            if isinstance(message, Mapping)
        )
        if not messages or not _tool_protocol_complete(messages):
            continue
        fingerprint = _fingerprint(messages)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        snapshots.append(messages)

    manager = TaskAwareContextManager(
        budget=ContextBudget(max_input_tokens=budget_tokens),
    )
    full_tokens = 0
    selected_tokens = 0
    immutable_preserved = True
    protocol_preserved = True
    budget_valid = True
    overflows = 0
    history_dropped = 0
    for messages in snapshots:
        full_tokens += estimate_message_tokens(messages)
        prepared = manager.prepare(messages, environment_state={}, task_state={})
        selected_tokens += int(prepared.audit["estimated_input_tokens"])
        original_task = next(
            (message.get("content") for message in messages if message.get("role") == "user"),
            None,
        )
        selected_task = next(
            (
                message.get("content")
                for message in prepared.messages
                if message.get("role") == "user"
            ),
            None,
        )
        immutable_preserved = immutable_preserved and original_task == selected_task
        protocol_preserved = protocol_preserved and _tool_protocol_complete(prepared.messages)
        overflow = bool(prepared.audit["immutable_overflow"])
        overflows += int(overflow)
        budget_valid = budget_valid and (
            int(prepared.audit["estimated_input_tokens"]) <= budget_tokens or overflow
        )
        history_dropped += int(prepared.audit["history_dropped"])

    reduction = 1 - selected_tokens / full_tokens if full_tokens else None
    return {
        "run": run_dir.name,
        "events_path": str(events_path),
        "snapshot_count": len(snapshots),
        "full_message_surface_tokens": full_tokens,
        "selected_message_surface_tokens": selected_tokens,
        "estimated_surface_reduction_fraction": reduction,
        "history_messages_dropped": history_dropped,
        "immutable_overflow_snapshots": overflows,
        "immutable_task_preserved": immutable_preserved,
        "tool_protocol_preserved": protocol_preserved,
        "budget_respected_or_explicit_overflow": budget_valid,
    }


def _normalize_message(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }.get(str(raw.get("type") or raw.get("role") or ""), str(raw.get("role") or ""))
    message: dict[str, Any] = {"role": role, "content": raw.get("content", "")}
    if role == "assistant" and raw.get("tool_calls"):
        message["tool_calls"] = [
            {
                "id": str(item.get("id")),
                "name": str(item.get("name")),
                "arguments": dict(item.get("arguments") or item.get("args") or {}),
            }
            for item in raw["tool_calls"]
            if isinstance(item, Mapping)
        ]
    if role == "tool" and raw.get("tool_call_id") is not None:
        message["tool_call_id"] = str(raw["tool_call_id"])
    return message


def _tool_protocol_complete(messages: Sequence[Mapping[str, Any]]) -> bool:
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            if pending:
                return False
            calls = message.get("tool_calls") or ()
            pending = {
                str(item.get("id"))
                for item in calls
                if isinstance(item, Mapping) and item.get("id") is not None
            }
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id not in pending:
                return False
            pending.remove(call_id)
        elif pending:
            return False
    return not pending


def _fingerprint(messages: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(
        list(messages),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
