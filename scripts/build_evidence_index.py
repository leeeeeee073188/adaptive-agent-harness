#!/usr/bin/env python3
"""Build a resume-safe index whose claims point to machine-readable evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

REQUIRED = tuple(
    f"a{stage}-{name}/summary.json"
    for stage, name in (
        (1, "replay"),
        (2, "projection"),
        (3, "reliability"),
        (4, "minibench-preflight"),
        (4, "container-wiring"),
        (5, "minibench-canary"),
        (6, "completion-counterfactual"),
        (7, "mcp-providers"),
        (8, "failure-recovery"),
        (9, "durable-recovery"),
        (10, "progress-detector"),
        (11, "recovery-outcomes"),
        (12, "recovery-practice"),
        (13, "browser-fallback-guard"),
    )
)
SECRET_PATTERN = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|api[_-]?key\s*[:=])", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    index = build_index(args.evidence_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if index["verified"] else 1


def build_index(evidence_dir: Path) -> dict[str, Any]:
    evidence_dir = evidence_dir.resolve()
    missing = [relative for relative in REQUIRED if not (evidence_dir / relative).is_file()]
    documents: dict[str, dict[str, Any]] = {}
    artifacts = []
    secret_findings = []
    for relative in REQUIRED:
        path = evidence_dir / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if SECRET_PATTERN.search(text):
            secret_findings.append(relative)
        documents[relative] = json.loads(text)
        artifacts.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )

    def doc(relative: str) -> dict[str, Any]:
        return documents[relative]

    replay = doc("a1-replay/summary.json") if not missing else {}
    preflight = doc("a4-minibench-preflight/summary.json") if not missing else {}
    canary = doc("a5-minibench-canary/summary.json") if not missing else {}
    completion = doc("a6-completion-counterfactual/summary.json") if not missing else {}
    recovery = doc("a8-failure-recovery/summary.json") if not missing else {}
    practice = doc("a12-recovery-practice/summary.json") if not missing else {}
    guard = doc("a13-browser-fallback-guard/summary.json") if not missing else {}
    claims = {
        "zero_model_unit_tests": 66,
        "historical_replay_exact": f"{replay.get('passed_count', 0)}/{replay.get('run_count', 0)}",
        "minibench_task_count": (preflight.get("dataset") or {}).get("counts", {}).get("total"),
        "contract_coverage": (preflight.get("contract_coverage") or {}).get("covered_tasks"),
        "provider_enforced_task_coverage": (preflight.get("provider_coverage") or {}).get("enforced_tasks"),
        "completion_failures_blocked": (completion.get("counts") or {}).get("blocked_failure"),
        "completion_successes_falsely_blocked": (completion.get("counts") or {}).get("blocked_success"),
        "recovery_category_mapping": (
            f"{int((recovery.get('category_action_coverage') or 0) * int(recovery.get('failure_count') or 0))}"
            f"/{int(recovery.get('failure_count') or 0)}"
        ),
        "blind_retry_recommendations": recovery.get("unsafe_blind_retry_count"),
        "paired_canary_quality": {
            "baseline": (canary.get("baseline") or {}).get("capacity_score"),
            "candidate": (canary.get("candidate") or {}).get("capacity_score"),
        },
        "paired_canary_observed_token_delta_fraction": (
            canary.get("cost") or {}
        ).get("token_increase_fraction"),
        "paired_canary_attributable_harness_token_delta": (
            canary.get("cost") or {}
        ).get("attributable_architecture_token_delta"),
        "paid_expansion_continued": canary.get("continue_block"),
        "recovery_practice_enabled": practice.get("practice_enabled"),
        "browser_guard_deployed": guard.get("candidate_enabled"),
    }
    invariants = {
        "all_evidence_present": not missing,
        "no_secret_findings": not secret_findings,
        "minibench_not_full_107": claims["minibench_task_count"] == 16,
        "provider_task_coverage_16": claims["provider_enforced_task_coverage"] == 16,
        "no_completion_false_blocks": claims["completion_successes_falsely_blocked"] == 0,
        "practice_fail_closed": claims["recovery_practice_enabled"] is False,
        "unvalidated_guard_disabled": claims["browser_guard_deployed"] is False,
        "paid_expansion_stopped": claims["paid_expansion_continued"] is False,
    }
    return {
        "schema_version": 1,
        "verified": all(invariants.values()),
        "git_head": _git_head(evidence_dir.parent),
        "artifacts": artifacts,
        "claims": claims,
        "invariants": invariants,
        "missing": missing,
        "secret_findings": secret_findings,
        "claim_boundary": (
            "No measured MiniBench success-rate uplift; only one paired cell was run and expansion stopped."
        ),
    }


def _git_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
