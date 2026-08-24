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
        (14, "exact-count-provider"),
        (15, "workbench-calendar-provider"),
        (16, "architecture-evolution"),
        (17, "task-aware-context"),
        (18, "tool-action-ledger"),
        (19, "tool-advice"),
        (20, "runtime-evolution"),
        (21, "live-v2-canary"),
        (22, "live-model-evolution"),
        (23, "runtime-failure-analysis"),
        (24, "next-generation-gate"),
        (25, "v3-container-conformance"),
        (26, "v3-live-canary"),
        (27, "v3-1-gate"),
        (28, "v3-1-container-conformance"),
    )
)
OPTIONAL = (
    "a20-runtime-evolution/container-wiring.json",
    "a20-runtime-evolution/review.json",
    "a22-live-model-evolution/pair-v2-6.json",
    "a22-live-model-evolution/review.json",
    "a25-v3-container-conformance/container-wiring.json",
    "a26-v3-live-canary/pair.json",
    "a26-v3-live-canary/diagnosis.json",
    "a28-v3-1-container-conformance/container-wiring.json",
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
    for relative in (*REQUIRED, *OPTIONAL):
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
    architecture = doc("a16-architecture-evolution/summary.json") if not missing else {}
    context = doc("a17-task-aware-context/summary.json") if not missing else {}
    actions = doc("a18-tool-action-ledger/summary.json") if not missing else {}
    advice = doc("a19-tool-advice/summary.json") if not missing else {}
    runtime_evolution = doc("a20-runtime-evolution/summary.json") if not missing else {}
    runtime_review = documents.get("a20-runtime-evolution/review.json", {})
    live_evolution = doc("a22-live-model-evolution/summary.json") if not missing else {}
    live_review = documents.get("a22-live-model-evolution/review.json", {})
    failure_analysis = doc("a23-runtime-failure-analysis/summary.json") if not missing else {}
    next_generation = doc("a24-next-generation-gate/summary.json") if not missing else {}
    v3_conformance = doc("a25-v3-container-conformance/summary.json") if not missing else {}
    v3_wiring = documents.get("a25-v3-container-conformance/container-wiring.json", {})
    v3_live = doc("a26-v3-live-canary/summary.json") if not missing else {}
    v3_1_gate = doc("a27-v3-1-gate/summary.json") if not missing else {}
    v3_1_conformance = doc("a28-v3-1-container-conformance/summary.json") if not missing else {}
    v3_1_wiring = documents.get("a28-v3-1-container-conformance/container-wiring.json", {})
    selected_live_row = next(
        (
            row
            for row in live_evolution.get("runs") or ()
            if row.get("run_id") == live_evolution.get("selected_candidate_run_id")
        ),
        None,
    )
    claims = {
        "zero_model_unit_tests": (runtime_review.get("verification") or {}).get(
            "adaptive_unit_tests"
        ),
        "tool_advice_stage_unit_tests": advice.get("test_count"),
        "current_realreplica_unit_tests": (
            runtime_review.get("verification") or {}
        ).get("realreplica_unit_tests"),
        "core_business_vocabulary_findings": architecture.get(
            "core_business_vocabulary_findings"
        ),
        "evolution_online_mutation_enabled": architecture.get(
            "online_profile_mutation_enabled"
        ),
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
        "context_historical_snapshot_count": (
            context.get("historical_counterfactual") or {}
        ).get("snapshot_count"),
        "context_median_estimated_surface_reduction_fraction": (
            context.get("historical_counterfactual") or {}
        ).get("median_run_estimated_surface_reduction_fraction"),
        "context_v1_1_quality": (context.get("context_v1_1") or {}).get("capacity_score"),
        "context_v1_1_token_delta_fraction": (
            context.get("context_v1_1") or {}
        ).get("token_delta_fraction_vs_exploratory_control"),
        "context_v1_1_promoted": (context.get("context_v1_1") or {}).get("decision")
        == "promote",
        "context_v1_2_status": (context.get("context_v1_2") or {}).get("status"),
        "context_v1_2_quality": (context.get("context_v1_2") or {}).get("capacity_score"),
        "context_v1_2_token_delta_fraction": (
            context.get("context_v1_2") or {}
        ).get("token_delta_fraction_vs_exploratory_control"),
        "context_v1_2_tool_call_delta": (context.get("context_v1_2") or {}).get(
            "tool_call_delta_vs_exploratory_control"
        ),
        "context_v1_2_decision": (context.get("context_v1_2") or {}).get("decision"),
        "context_stage_minibench_tasks_executed": (context.get("gates") or {}).get(
            "minibench_tasks_executed_this_stage"
        ),
        "action_ledger_records": (actions.get("historical_counterfactual") or {}).get(
            "action_records"
        ),
        "action_ledger_classified_fraction": (
            actions.get("historical_counterfactual") or {}
        ).get("classified_fraction"),
        "action_ledger_verification_warnings": (
            actions.get("historical_counterfactual") or {}
        ).get("verification_warnings"),
        "action_ledger_stable_control_warnings": (
            (actions.get("historical_counterfactual") or {}).get("stable_pass_controls")
            or {}
        ).get("warnings"),
        "action_ledger_enforcement_ready": (actions.get("v1_3") or {}).get(
            "enforcement_ready"
        ),
        "action_ledger_v1_3_status": (actions.get("v1_3") or {}).get("status"),
        "action_ledger_v1_3_paid_runs": (actions.get("v1_3") or {}).get("paid_runs"),
        "tool_advice_gate_eligible": (advice.get("advice_gate") or {}).get("eligible"),
        "tool_enforcement_gate_eligible": (advice.get("advice_gate") or {}).get(
            "enforcement_eligible"
        ),
        "tool_advice_v1_4_promoted": (advice.get("v1_4") or {}).get("decision")
        == "promote",
        "tool_advice_v1_5_status": (advice.get("v1_5") or {}).get("status"),
        "tool_advice_v1_5_quality": (advice.get("v1_5") or {}).get("capacity_score"),
        "tool_advice_v1_5_token_delta_fraction": (advice.get("v1_5") or {}).get(
            "token_delta_fraction_vs_exploratory_control"
        ),
        "tool_advice_v1_5_tool_delta": (advice.get("v1_5") or {}).get(
            "tool_call_delta_vs_exploratory_control"
        ),
        "tool_advice_v1_5_advice_applied": (advice.get("v1_5") or {}).get(
            "advice_applied"
        ),
        "tool_advice_second_task_started": (advice.get("gates") or {}).get(
            "second_task_started"
        ),
        "runtime_conformance_passed": (
            runtime_evolution.get("runtime_conformance") or {}
        ).get("passed"),
        "runtime_conformance_test_count": (
            runtime_evolution.get("runtime_conformance") or {}
        ).get("test_count"),
        "runtime_evolution_zero_model_passed": runtime_evolution.get(
            "zero_model_passed"
        ),
        "runtime_evolution_model_calls": runtime_evolution.get("model_calls"),
        "runtime_evolution_role_counts": (
            (runtime_evolution.get("dataset") or {}).get("counts") or {}
        ).get("evaluation_role"),
        "runtime_evolution_wiring_passed": (
            runtime_evolution.get("container_wiring") or {}
        ).get("passed"),
        "runtime_evolution_baseline_bootstrap_allowed": runtime_evolution.get(
            "baseline_bootstrap_allowed"
        ),
        "runtime_evolution_paid_candidate_allowed": (
            runtime_evolution.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "runtime_evolution_single_canary_default": runtime_evolution.get(
            "single_canary_default"
        ),
        "runtime_evolution_review_passed": runtime_review.get("passed"),
        "live_completed_run_count": live_evolution.get("completed_run_count"),
        "live_task_id": live_evolution.get("task_id"),
        "live_full_107_run": live_evolution.get("full_107_run"),
        "live_observed_total_tokens": live_evolution.get("observed_total_tokens"),
        "live_selected_candidate_variant": live_evolution.get(
            "selected_candidate_variant"
        ),
        "live_selected_candidate_decision": live_evolution.get(
            "selected_candidate_decision"
        ),
        "live_paid_expansion_allowed": live_evolution.get("paid_expansion_allowed"),
        "live_review_passed": live_review.get("passed"),
        "live_selected_row_present": selected_live_row is not None,
        "live_selected_passed": (selected_live_row or {}).get("passed"),
        "live_selected_capacity_score": (selected_live_row or {}).get(
            "capacity_score"
        ),
        "live_selected_checks_passed": (selected_live_row or {}).get(
            "checks_passed"
        ),
        "live_selected_checks_total": (selected_live_row or {}).get("checks_total"),
        "live_selected_token_fraction_vs_baseline": (
            (selected_live_row or {}).get("delta_vs_baseline") or {}
        ).get("token_fraction"),
        "failure_analysis_model_calls": failure_analysis.get("analysis_model_calls"),
        "failure_analysis_run_count": failure_analysis.get("run_count"),
        "failure_analysis_v2_6_signals": next(
            (
                row.get("signals")
                for row in failure_analysis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_runtime_evolution_v2_6"
            ),
            None,
        ),
        "failure_analysis_v2_7_signals": next(
            (
                row.get("signals")
                for row in failure_analysis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_runtime_evolution_v2_7"
            ),
            None,
        ),
        "next_generation_gate_passed": next_generation.get("passed"),
        "next_generation_single_canary_allowed": next_generation.get(
            "candidate_single_development_canary_allowed"
        ),
        "next_generation_paid_expansion_allowed": next_generation.get(
            "paid_expansion_allowed"
        ),
        "next_generation_visible_workspace_selected": (
            next_generation.get("context_replay") or {}
        ).get("visible_workspace_selected"),
        "next_generation_source_criterion_count": (
            next_generation.get("contract") or {}
        ).get("source_access_criterion_count"),
        "next_generation_historical_artifact_rejected": (
            next_generation.get("historical_artifact") or {}
        ).get("rejected"),
        "v3_candidate_variant": (
            (v3_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_paid_canary_allowed": (
            v3_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_zero_model_passed": v3_conformance.get("zero_model_passed"),
        "v3_source_hash_matches": bool(next_generation.get("adaptive_source_sha256"))
        and next_generation.get("adaptive_source_sha256")
        == v3_wiring.get("adaptive_source_sha256"),
        "v3_profile_fingerprint_matches": bool(
            next_generation.get("executable_policy_profile_fingerprint")
        )
        and next_generation.get("executable_policy_profile_fingerprint")
        == v3_wiring.get("executable_policy_profile_fingerprint"),
        "v3_live_selected_candidate": v3_live.get("selected_candidate_variant"),
        "v3_live_selected_decision": v3_live.get("selected_candidate_decision"),
        "v3_live_paid_expansion_allowed": v3_live.get("paid_expansion_allowed"),
        "v3_live_row": next(
            (
                row
                for row in v3_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3"
            ),
            None,
        ),
        "v3_1_gate_passed": v3_1_gate.get("passed"),
        "v3_1_single_canary_allowed": v3_1_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_1_paid_expansion_allowed": v3_1_gate.get("paid_expansion_allowed"),
        "v3_1_candidate_variant": (
            (v3_1_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_1_paid_canary_allowed": (
            v3_1_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_1_source_hash_matches": bool(v3_1_gate.get("adaptive_source_sha256"))
        and v3_1_gate.get("adaptive_source_sha256")
        == v3_1_wiring.get("adaptive_source_sha256"),
        "v3_1_profile_fingerprint_matches": bool(
            v3_1_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_1_gate.get("executable_policy_profile_fingerprint")
        == v3_1_wiring.get("executable_policy_profile_fingerprint"),
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
        "core_business_semantics_decoupled": claims["core_business_vocabulary_findings"] == 0,
        "evolution_is_offline_governed": claims["evolution_online_mutation_enabled"] is False,
        "context_cost_regression_not_promoted": claims["context_v1_1_promoted"] is False,
        "context_next_candidate_stays_shadow": claims["context_v1_2_status"]
        == "shadow_observed"
        and claims["context_v1_2_decision"] == "keep_shadow",
        "context_stage_not_full_benchmark": claims["context_stage_minibench_tasks_executed"] == 1,
        "action_ledger_observe_only": claims["action_ledger_enforcement_ready"] is False,
        "action_ledger_stable_controls_unwarned": claims[
            "action_ledger_stable_control_warnings"
        ]
        == 0,
        "action_ledger_v1_3_unexecuted": claims["action_ledger_v1_3_status"]
        == "shadow_unexecuted"
        and claims["action_ledger_v1_3_paid_runs"] == 0,
        "tool_advice_not_enforcement": claims["tool_advice_gate_eligible"] is True
        and claims["tool_enforcement_gate_eligible"] is False,
        "tool_advice_cost_regression_not_promoted": claims["tool_advice_v1_4_promoted"]
        is False,
        "tool_advice_v1_5_stays_shadow": claims["tool_advice_v1_5_status"]
        == "shadow_observed",
        "tool_advice_did_not_expand": claims["tool_advice_second_task_started"] is False,
        "runtime_conformance_verified": claims["runtime_conformance_passed"] is True,
        "runtime_evolution_zero_model_only": claims["runtime_evolution_zero_model_passed"]
        is True
        and claims["runtime_evolution_model_calls"] == 0,
        "runtime_evolution_partition_isolated": claims["runtime_evolution_role_counts"]
        == {"development": 8, "heldout": 4, "transfer": 4},
        "runtime_evolution_wiring_refreshed": claims["runtime_evolution_wiring_passed"]
        is True,
        "runtime_evolution_candidate_stays_disabled": claims[
            "runtime_evolution_paid_candidate_allowed"
        ]
        is False,
        "runtime_evolution_single_canary_default": claims[
            "runtime_evolution_single_canary_default"
        ]
        is True,
        "runtime_evolution_review_cleared": claims["runtime_evolution_review_passed"]
        is True,
        "live_test_remains_single_task": (claims["live_completed_run_count"] or 0) >= 1
        and claims["live_task_id"] == "cli-google-trends-data-quality-audit"
        and claims["live_full_107_run"] is False,
        "live_selected_row_resolves": claims["live_selected_row_present"] is True,
        "live_review_cleared": claims["live_review_passed"] is True,
        "live_candidate_not_overclaimed": claims["live_selected_candidate_decision"]
        == "keep_shadow"
        and claims["live_selected_passed"] is False
        and claims["live_paid_expansion_allowed"] is False,
        "live_candidate_has_partial_measured_gain": (
            claims["live_selected_capacity_score"] == 0.4
            and claims["live_selected_checks_passed"] == 2
            and claims["live_selected_checks_total"] == 5
            and (claims["live_selected_token_fraction_vs_baseline"] or 0) < 0
        ),
        "failure_analysis_is_zero_model": claims["failure_analysis_model_calls"] == 0,
        "failure_analysis_covers_live_chain": claims["failure_analysis_run_count"] == 6,
        "failure_analysis_proves_context_starvation": (
            (claims["failure_analysis_v2_6_signals"] or {}).get(
                "durable_failure_state_missing_from_model_context"
            )
            is True
        ),
        "failure_analysis_proves_cross_turn_regression": (
            (claims["failure_analysis_v2_7_signals"] or {}).get(
                "mutation_epoch_regression"
            )
            is True
        ),
        "next_generation_gate_cleared": claims["next_generation_gate_passed"] is True,
        "next_generation_is_single_canary_only": claims[
            "next_generation_single_canary_allowed"
        ]
        is True
        and claims["next_generation_paid_expansion_allowed"] is False,
        "next_generation_context_and_contract_verified": claims[
            "next_generation_visible_workspace_selected"
        ]
        is True
        and claims["next_generation_source_criterion_count"] == 2
        and claims["next_generation_historical_artifact_rejected"] is True,
        "v3_container_and_candidate_gate_cleared": claims["v3_zero_model_passed"]
        is True
        and claims["v3_paid_canary_allowed"] is True
        and claims["v3_candidate_variant"] == "adaptive_harness_evidence_workspace_v3",
        "v3_evidence_bound_to_current_source": claims["v3_source_hash_matches"] is True
        and claims["v3_profile_fingerprint_matches"] is True,
        "v3_regression_not_promoted": (
            (claims["v3_live_row"] or {}).get("passed") is False
            and (claims["v3_live_row"] or {}).get("capacity_score") == 0.0
            and (claims["v3_live_row"] or {}).get("output_file_count") == 0
            and claims["v3_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_live_selected_decision"] == "keep_shadow"
            and claims["v3_live_paid_expansion_allowed"] is False
        ),
        "v3_1_gate_is_single_canary_only": claims["v3_1_gate_passed"] is True
        and claims["v3_1_single_canary_allowed"] is True
        and claims["v3_1_paid_expansion_allowed"] is False,
        "v3_1_container_gate_cleared": claims["v3_1_candidate_variant"]
        == "adaptive_harness_evidence_workspace_v3_1"
        and claims["v3_1_paid_canary_allowed"] is True
        and claims["v3_1_source_hash_matches"] is True
        and claims["v3_1_profile_fingerprint_matches"] is True,
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
            "No measured MiniBench-wide success-rate uplift. Context v1/v1.1 each ran one Development "
            "task as exploratory Shadows; v1.1 recovered quality but was rejected for cost. v1.2 "
            "recovered the exact output at +9.23% directional Token cost, but remains Shadow because "
            "tool/latency cost is high and the sample is neither fresh-paired nor sufficient. No full "
            "107-task run was performed. Tool Action Ledger v1.3 has only zero-model observe-only "
            "evidence and is not claimed to reduce calls. Non-blocking Advice v1.4 was rejected for "
            "cost; v1.5 had near-baseline directional Tokens but no Advice trigger and remains Shadow. "
            "A20 proves Runtime Conformance, trusted Ledger-to-Rollout conversion, offline Distiller "
            "boundaries, 8/4/4 partition integrity, and refreshed zero-model container wiring. It does "
            "not claim MiniBench-wide task-success uplift. A21/A22 record paid single-task iterations: "
            "v2.6 improved public capacity from 0.0 to 0.4 with lower observed Tokens, but still failed "
            "and remains Shadow. A23 derives the context-starvation, repeated-resource, artifact-copy, "
            "and cross-turn lifecycle failure signals with zero model calls. A24/A25 verify the v3 "
            "Visible Evidence Workspace, public contract, source obligations, soft phase/recovery, and "
            "container wiring. A26 records that v3 nevertheless regressed to 0/5 and was not promoted. "
            "A27/A28 bind the source-first phase, local-resource governor, and three-turn v3.1 profile "
            "to one replacement Development canary only. Transfer, Held-out, and further task expansion "
            "remain disabled."
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
