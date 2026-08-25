#!/usr/bin/env python3
"""Aggregate public Diagnostic4 run evidence without reading evaluator-private data."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

FORBIDDEN_PARTS = {"private", "verifier", "reward", "grader", "rubric"}


def _read_json(path: Path) -> Any:
    _reject_private(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_private(path: Path) -> None:
    if any(part.lower() in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError(f"forbidden evaluator-private path: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ledger_rows(path: Path) -> list[dict[str, Any]]:
    _reject_private(path)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _required_artifacts(rows: list[dict[str, Any]]) -> list[str]:
    for event in rows:
        if event.get("type") != "task/contract-created":
            continue
        contract = (event.get("payload") or {}).get("contract") or {}
        return [
            str((criterion.get("parameters") or {}).get("path"))
            for criterion in contract.get("criteria") or ()
            if criterion.get("kind") == "artifact_exists"
        ]
    return []


def _output_inventory(task_run: Path) -> list[dict[str, Any]]:
    outputs = task_run / "workspace/outputs"
    if not outputs.is_dir():
        return []
    root = outputs.resolve()
    inventory = []
    for path in sorted(outputs.rglob("*")):
        relative = path.relative_to(outputs)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts):
            continue
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            inventory.append(
                {
                    "path": f"outputs/{relative.as_posix()}",
                    "bytes": resolved.stat().st_size,
                    "sha256": _sha256(resolved),
                }
            )
        except OSError:
            continue
    return inventory


def _matches_required(output_path: str, required_path: str) -> bool:
    normalized = required_path.removeprefix("/task/")
    return (
        output_path.startswith(normalized.rstrip("/") + "/")
        if normalized.endswith("/")
        else output_path == normalized
    )


def analyze_run(run_dir: Path) -> dict[str, Any]:
    summary = _read_json(run_dir / "summary.json")
    if summary.get("total") != 1 or len(summary.get("results") or ()) != 1:
        raise ValueError(f"Diagnostic run must contain exactly one task: {run_dir}")
    result = summary["results"][0]
    task_run = Path(result["run_dir"]).resolve()
    if not task_run.is_relative_to(run_dir.resolve()):
        raise ValueError(f"task run escapes diagnostic run: {task_run}")
    ledger = _ledger_rows(task_run / "agent/adaptive-ledger.jsonl")
    integrity_passed = result.get("integrity_passed")
    if integrity_passed is None and (task_run / "integrity.json").is_file():
        integrity_passed = _read_json(task_run / "integrity.json").get("passed")
    required = _required_artifacts(ledger)
    outputs = _output_inventory(task_run)
    output_paths = [item["path"] for item in outputs]
    matched_required = [
        path
        for path in required
        if any(_matches_required(output_path, path) for output_path in output_paths)
    ]
    missing_required = [path for path in required if path not in matched_required]
    unrelated_outputs = [
        path
        for path in output_paths
        if not any(_matches_required(path, required_path) for required_path in required)
    ]
    event_types = Counter(str(event.get("type")) for event in ledger)
    action_intents: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    failure_types: Counter[str] = Counter()
    recovery_primaries: Counter[str] = Counter()
    recovery_actions: Counter[str] = Counter()
    max_history_dropped = 0
    max_context_tokens = 0
    completion_missing: list[str] = []
    completion_assessments: list[dict[str, Any]] = []
    runtime_errors: list[dict[str, str]] = []
    for event in ledger:
        payload = event.get("payload") or {}
        if event.get("type") == "tool/action-audited":
            record = payload.get("record") or {}
            decision = payload.get("decision") or {}
            action_intents[str(record.get("intent") or "unknown")] += 1
            if record.get("error_type"):
                tool_errors[str(record["error_type"])] += 1
            dispositions[str(decision.get("disposition") or "unknown")] += 1
        elif event.get("type") == "failure/classified":
            failure = payload.get("failure") or {}
            failure_types[str(failure.get("error_type") or "UNKNOWN")] += 1
        elif event.get("type") == "recovery/decided":
            recovery_primaries[str(payload.get("primary") or "UNKNOWN")] += 1
            recovery_actions.update(str(item) for item in payload.get("actions") or ())
        elif event.get("type") == "context/selected":
            max_history_dropped = max(max_history_dropped, int(payload.get("history_dropped") or 0))
            max_context_tokens = max(max_context_tokens, int(payload.get("estimated_input_tokens") or 0))
        elif event.get("type") == "completion/checked":
            completion_missing = [str(item) for item in payload.get("missing") or ()]
            completion_assessments = [
                {
                    "criterion_id": item.get("criterion_id"),
                    "status": item.get("status"),
                }
                for item in payload.get("assessments") or ()
            ]
        elif event.get("type") == "runtime/error":
            runtime_errors.append(
                {
                    "type": str(payload.get("type") or "UNKNOWN"),
                    "message": str(payload.get("message") or ""),
                }
            )
    required_directories = [path for path in required if path.endswith("/")]
    directory_exists_but_unsatisfied = []
    for path in required_directories:
        directory = task_run / "workspace" / path.removeprefix("/task/")
        criterion_id_fragment = path.rstrip("/").replace("/", "-").replace("_", "-")
        if directory.is_dir() and any(
            criterion_id_fragment in str(item.get("criterion_id"))
            and item.get("status") != "satisfied"
            for item in completion_assessments
        ):
            directory_exists_but_unsatisfied.append(path)
    usage = dict(result.get("usage") or {})
    return {
        "run_id": summary.get("run_id"),
        "task_id": result.get("task_id"),
        "category": str(result.get("task_id") or "unknown").split("-", 1)[0],
        "modality": result.get("modality"),
        "passed": result.get("passed"),
        "capacity_score": result.get("capacity_score"),
        "checks_passed": result.get("checks_passed"),
        "checks_total": result.get("checks_total"),
        "elapsed_sec": result.get("elapsed_sec"),
        "usage": usage,
        "tool_calls": result.get("tool_call_count"),
        "agent_exec_returncode": result.get("agent_exec_returncode"),
        "integrity_passed": integrity_passed,
        "llm_judge_score": result.get("llm_judge_score"),
        "ledger_event_count": len(ledger),
        "model_call_audits": event_types["context/selected"],
        "turns": event_types["turn/start"],
        "action_intents": dict(sorted(action_intents.items())),
        "tool_errors": dict(sorted(tool_errors.items())),
        "dispositions": dict(sorted(dispositions.items())),
        "failure_types": dict(sorted(failure_types.items())),
        "recovery_primaries": dict(sorted(recovery_primaries.items())),
        "recovery_actions": dict(sorted(recovery_actions.items())),
        "max_history_dropped": max_history_dropped,
        "max_context_tokens": max_context_tokens,
        "required_artifacts": required,
        "matched_required_artifacts": matched_required,
        "missing_required_artifacts": missing_required,
        "output_inventory": outputs,
        "unrelated_output_paths": unrelated_outputs,
        "required_directory_exists_but_unsatisfied": directory_exists_but_unsatisfied,
        "completion_missing": completion_missing,
        "completion_assessments": completion_assessments,
        "runtime_errors": runtime_errors,
    }


def build_report(
    run_dirs: list[Path],
    *,
    scope: str = "Diagnostic4 public cross-type failure synthesis",
    claim_boundary: str = (
        "Four first-sample Development diagnostics only. These results identify cross-type "
        "failure surfaces; they do not estimate MiniBench or full-benchmark success rate."
    ),
) -> dict[str, Any]:
    rows = [analyze_run(path.resolve()) for path in run_dirs]
    categories = Counter(str(row["category"]) for row in rows)
    total_tokens = sum(int((row.get("usage") or {}).get("total_tokens") or 0) for row in rows)
    total_input = sum(int((row.get("usage") or {}).get("input_tokens") or 0) for row in rows)
    total_output = sum(int((row.get("usage") or {}).get("output_tokens") or 0) for row in rows)
    missing_runs = [row["run_id"] for row in rows if row["missing_required_artifacts"]]
    wrong_target_runs = [
        row["run_id"]
        for row in rows
        if row["unrelated_output_paths"] and row["missing_required_artifacts"]
    ]
    runtime_error_runs = [row["run_id"] for row in rows if row["runtime_errors"]]
    directory_semantic_runs = [
        row["run_id"]
        for row in rows
        if row["required_directory_exists_but_unsatisfied"]
    ]
    architecture_gaps = []
    if missing_runs:
        architecture_gaps.append(
            {
                "dimension": "artifact_synthesis",
                "evidence": "required artifacts remained missing across task types",
                "affected_runs": missing_runs,
            }
        )
    if wrong_target_runs:
        architecture_gaps.append(
            {
                "dimension": "artifact_targeting",
                "evidence": "outputs were written outside the required artifact targets",
                "affected_runs": wrong_target_runs,
            }
        )
    if directory_semantic_runs:
        architecture_gaps.append(
            {
                "dimension": "artifact_contract",
                "evidence": "a required output directory existed with files but remained unsatisfied",
                "affected_runs": directory_semantic_runs,
            }
        )
    if runtime_error_runs:
        architecture_gaps.append(
            {
                "dimension": "environment_observation",
                "evidence": (
                    "a turn observation error escaped the provider boundary before completion"
                ),
                "affected_runs": runtime_error_runs,
            }
        )
    loop_runs = [
        row["run_id"]
        for row in rows
        if row["tool_errors"] or row["failure_types"].get("NO_PROGRESS")
    ]
    if loop_runs:
        architecture_gaps.append(
            {
                "dimension": "tool_and_loop_control",
                "evidence": (
                    "tool failures and no-progress events consumed model calls without "
                    "completing required artifacts"
                ),
                "affected_runs": loop_runs,
            }
        )
    ranked_gaps = [
        {"rank": rank, **item}
        for rank, item in enumerate(architecture_gaps, start=1)
    ]
    return {
        "schema_version": 1,
        "scope": scope,
        "analysis_model_calls": 0,
        "analysis_new_tokens": 0,
        "run_count": len(rows),
        "category_counts": dict(sorted(categories.items())),
        "all_integrity_passed": all(row["integrity_passed"] is True for row in rows),
        "passed_runs": sum(row["passed"] is True for row in rows),
        "capacity_score_mean": (
            sum(float(row["capacity_score"] or 0.0) for row in rows) / len(rows)
            if rows
            else None
        ),
        "cost": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "input_fraction": total_input / total_tokens if total_tokens else None,
            "tool_calls": sum(int(row["tool_calls"] or 0) for row in rows),
            "elapsed_sec": sum(float(row["elapsed_sec"] or 0.0) for row in rows),
        },
        "cross_type_signals": {
            "missing_required_artifact_runs": missing_runs,
            "wrong_target_output_runs": wrong_target_runs,
            "required_directory_semantics_runs": directory_semantic_runs,
            "runtime_error_runs": runtime_error_runs,
            "tool_error_count": sum(
                sum(int(count) for count in row["tool_errors"].values()) for row in rows
            ),
            "no_progress_failure_count": sum(
                int(row["failure_types"].get("NO_PROGRESS", 0)) for row in rows
            ),
            "loop_failure_count": sum(
                int(row["failure_types"].get("LOOP", 0)) for row in rows
            ),
        },
        "ranked_architecture_gaps": ranked_gaps,
        "claim_boundary": claim_boundary,
        "paid_expansion_allowed": False,
        "runs": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--scope",
        default="Diagnostic4 public cross-type failure synthesis",
    )
    parser.add_argument(
        "--claim-boundary",
        default=(
            "Four first-sample Development diagnostics only. These results identify cross-type "
            "failure surfaces; they do not estimate MiniBench or full-benchmark success rate."
        ),
    )
    args = parser.parse_args()
    report = build_report(
        args.run_dirs,
        scope=args.scope,
        claim_boundary=args.claim_boundary,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
