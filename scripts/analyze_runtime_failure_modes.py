#!/usr/bin/env python3
"""Summarize generic Harness failure signals from public run artifacts only."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = analyze_failure_modes(args.run_dirs, args.task_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def analyze_failure_modes(run_dirs: list[Path], task_id: str) -> dict[str, Any]:
    runs = []
    skipped = []
    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        if not summary_path.is_file():
            skipped.append({"run_dir": str(run_dir), "reason": "summary missing"})
            continue
        summary = _read_json(summary_path)
        matches = [
            row
            for row in summary.get("results") or ()
            if row.get("task_id") == task_id
        ]
        if len(matches) != 1:
            skipped.append(
                {
                    "run_id": summary.get("run_id"),
                    "run_dir": str(run_dir),
                    "reason": "completed task result missing or ambiguous",
                }
            )
            continue
        result = matches[0]
        task_dir = Path(result["run_dir"])
        ledger_path = task_dir / "agent/adaptive-ledger.jsonl"
        if not ledger_path.is_file():
            skipped.append(
                {
                    "run_id": summary.get("run_id"),
                    "run_dir": str(run_dir),
                    "reason": "adaptive ledger missing",
                }
            )
            continue
        runs.append(_analyze_run(summary, result, task_dir, _read_jsonl(ledger_path)))
    if not runs:
        raise ValueError("no completed adaptive runs with ledgers")
    return {
        "scope": "zero-model generic Runtime failure analysis",
        "task_id": task_id,
        "analysis_model_calls": 0,
        "analysis_new_tokens": 0,
        "run_count": len(runs),
        "skipped_runs": skipped,
        "runs": runs,
        "claim_boundary": (
            "Signals are derived from public run summaries, adaptive ledgers, and workspace "
            "artifacts. No evaluation-private values or explanations are read."
        ),
    }


def _analyze_run(
    summary: dict[str, Any],
    result: dict[str, Any],
    task_dir: Path,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    action_records = [
        event.get("payload", {}).get("record")
        for event in events
        if event.get("type") == "tool/action-audited"
        and isinstance(event.get("payload", {}).get("record"), dict)
    ]
    resource_counts: Counter[str] = Counter()
    resource_tools: dict[str, set[str]] = defaultdict(set)
    for record in action_records:
        for resource in record.get("resources") or ():
            normalized = str(resource)
            resource_counts[normalized] += 1
            resource_tools[normalized].add(str(record.get("tool_name") or "unknown"))

    context_audits = [
        event.get("payload") or {}
        for event in events
        if event.get("type") == "context/selected"
        and (event.get("payload") or {}).get("adapter")
    ]
    failures = [
        (event.get("payload", {}).get("failure") or {})
        for event in events
        if event.get("type") == "failure/classified"
    ]
    runtime_errors = [
        event.get("payload") or {}
        for event in events
        if event.get("type") == "runtime/error"
    ]
    contracts = [
        event.get("payload", {}).get("contract") or {}
        for event in events
        if event.get("type") == "task/contract-created"
    ]
    required_kinds = sorted(
        {
            str(criterion.get("kind"))
            for contract in contracts
            for criterion in contract.get("criteria") or ()
            if criterion.get("required", True)
        }
    )
    repeated_resources = [
        {
            "resource": resource,
            "access_count": count,
            "tool_names": sorted(resource_tools[resource]),
        }
        for resource, count in resource_counts.most_common()
        if count > 1
    ]
    max_failure_tokens = max(
        [int((audit.get("layer_tokens") or {}).get("failure") or 0) for audit in context_audits]
        or [0]
    )
    usage = result.get("usage") or {}
    return {
        "run_id": summary.get("run_id"),
        "variant": (summary.get("experiment") or {}).get("variant"),
        "passed": result.get("passed") is True,
        "capacity_score": float(result.get("capacity_score") or 0.0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "tool_calls": int(result.get("tool_call_count") or 0),
        "audited_actions": len(action_records),
        "actions_by_intent": dict(
            sorted(Counter(str(record.get("intent") or "unknown") for record in action_records).items())
        ),
        "failure_types": dict(
            sorted(Counter(str(failure.get("error_type") or "UNKNOWN") for failure in failures).items())
        ),
        "runtime_error_types": sorted(
            {str(error.get("type") or "UNKNOWN") for error in runtime_errors}
        ),
        "required_contract_kinds": required_kinds,
        "repeated_resources": repeated_resources[:20],
        "context": {
            "model_call_audits": len(context_audits),
            "max_history_dropped": max(
                [int(audit.get("history_dropped") or 0) for audit in context_audits] or [0]
            ),
            "max_failure_layer_tokens": max_failure_tokens,
        },
        "artifact_matches_non_output_files": _matching_artifacts(task_dir),
        "signals": {
            "artifact_only_contract": required_kinds == ["artifact_exists"],
            "durable_failure_state_missing_from_model_context": bool(failures)
            and bool(context_audits)
            and max_failure_tokens == 0,
            "repeated_resource_accesses": sum(count - 1 for count in resource_counts.values() if count > 1),
            "cross_tool_repeated_resource_count": sum(
                count > 1 and len(resource_tools[resource]) > 1
                for resource, count in resource_counts.items()
            ),
            "mutation_epoch_regression": any(
                error.get("message") == "mutation epoch cannot move backwards"
                for error in runtime_errors
            ),
        },
    }


def _matching_artifacts(task_dir: Path) -> list[dict[str, str]]:
    workspace = task_dir / "workspace"
    outputs = workspace / "outputs"
    if not outputs.is_dir():
        return []
    source_hashes: dict[str, list[str]] = defaultdict(list)
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_relative_to(outputs):
            continue
        source_hashes[_sha256(path)].append(path.relative_to(workspace).as_posix())
    matches = []
    for output in sorted(outputs.rglob("*")):
        if not output.is_file():
            continue
        for source in source_hashes.get(_sha256(output), ()):
            matches.append(
                {
                    "output": output.relative_to(workspace).as_posix(),
                    "matching_source": source,
                }
            )
    return matches


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
