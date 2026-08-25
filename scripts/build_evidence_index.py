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

ROOT = Path(__file__).resolve().parents[1]

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
        (29, "v3-1-live-canary"),
        (30, "v3-2-gate"),
        (31, "v3-2-container-conformance"),
        (32, "v3-2-live-canary"),
        (33, "v3-3-gate"),
        (34, "v3-3-container-conformance"),
        (35, "v3-3-live-canary"),
        (36, "v3-4-thinking-max-gate"),
        (37, "v3-4-thinking-max-container"),
        (38, "v3-4-thinking-max-live"),
        (39, "mutation-epoch-offline"),
        (40, "v3-5-thinking-high-gate"),
        (41, "v3-5-thinking-high-container"),
        (42, "v3-5-thinking-high-live"),
        (43, "source-grounding-gate"),
        (44, "source-grounding-container"),
        (45, "v3-6-live"),
        (46, "v3-7-grounding-recovery-gate"),
        (47, "v3-7-grounding-recovery-container"),
        (48, "v3-7-live"),
        (49, "v3-8-strict-delivery-gate"),
        (50, "v3-8-strict-delivery-container"),
        (51, "v3-8-live"),
        (52, "v3-9-artifact-repair-gate"),
        (53, "v3-9-artifact-repair-container"),
        (54, "diagnostic4-preflight"),
        (55, "diagnostic4-live"),
        (56, "v4-cross-type-gate"),
        (57, "v4-cross-type-container"),
        (58, "v4-file-live"),
        (59, "v4-1-tool-compat-gate"),
        (60, "v4-1-tool-compat-container"),
        (61, "v4-1-file-live"),
        (62, "transform-manifest"),
        (63, "v4-2-transform-evidence-gate"),
        (64, "v4-2-transform-evidence-container"),
        (65, "deerflow-runtime-attribution-gate"),
        (66, "deerflow-runtime-attribution-live"),
        (67, "v4-2-minibench16-live"),
        (68, "runtime-capabilities"),
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
    "a29-v3-1-live-canary/pair.json",
    "a29-v3-1-live-canary/diagnosis.json",
    "a31-v3-2-container-conformance/container-wiring.json",
    "a32-v3-2-live-canary/pair.json",
    "a32-v3-2-live-canary/diagnosis.json",
    "a34-v3-3-container-conformance/container-wiring.json",
    "a34-v3-3-container-conformance/review.json",
    "a34-v3-3-container-conformance/verification.json",
    "a34-v3-3-container-conformance/readiness.json",
    "a35-v3-3-live-canary/pair.json",
    "a35-v3-3-live-canary/diagnosis.json",
    "a37-v3-4-thinking-max-container/container-wiring.json",
    "a36-v3-4-thinking-max-gate/deepseek-thinking-contract.json",
    "a37-v3-4-thinking-max-container/review.json",
    "a37-v3-4-thinking-max-container/verification.json",
    "a37-v3-4-thinking-max-container/readiness.json",
    "a38-v3-4-thinking-max-live/pair.json",
    "a38-v3-4-thinking-max-live/diagnosis.json",
    "a39-mutation-epoch-offline/zero-model-gate.json",
    "a39-mutation-epoch-offline/container-wiring.json",
    "a41-v3-5-thinking-high-container/container-wiring.json",
    "a41-v3-5-thinking-high-container/review.json",
    "a41-v3-5-thinking-high-container/verification.json",
    "a41-v3-5-thinking-high-container/lint-delta.json",
    "a41-v3-5-thinking-high-container/readiness.json",
    "a42-v3-5-thinking-high-live/pair.json",
    "a42-v3-5-thinking-high-live/diagnosis.json",
    "a44-source-grounding-container/container-wiring.json",
    "a44-source-grounding-container/review.json",
    "a44-source-grounding-container/verification.json",
    "a44-source-grounding-container/readiness.json",
    "a45-v3-6-live/pair.json",
    "a45-v3-6-live/diagnosis.json",
    "a47-v3-7-grounding-recovery-container/container-wiring.json",
    "a47-v3-7-grounding-recovery-container/review.json",
    "a47-v3-7-grounding-recovery-container/verification.json",
    "a47-v3-7-grounding-recovery-container/readiness.json",
    "a48-v3-7-live/pair.json",
    "a48-v3-7-live/diagnosis.json",
    "a50-v3-8-strict-delivery-container/container-wiring.json",
    "a50-v3-8-strict-delivery-container/review.json",
    "a50-v3-8-strict-delivery-container/verification.json",
    "a50-v3-8-strict-delivery-container/readiness.json",
    "a51-v3-8-live/pair.json",
    "a51-v3-8-live/diagnosis.json",
    "a53-v3-9-artifact-repair-container/container-wiring.json",
    "a53-v3-9-artifact-repair-container/review.json",
    "a54-diagnostic4-preflight/lint-delta.json",
    "a54-diagnostic4-preflight/review.json",
    "a54-diagnostic4-preflight/verification.json",
    "a54-diagnostic4-preflight/readiness.json",
    "a57-v4-cross-type-container/container-wiring.json",
    "a57-v4-cross-type-container/lint-delta.json",
    "a57-v4-cross-type-container/review.json",
    "a57-v4-cross-type-container/verification.json",
    "a57-v4-cross-type-container/readiness.json",
    "a58-v4-file-live/pair.json",
    "a58-v4-file-live/diagnosis.json",
    "a60-v4-1-tool-compat-container/container-wiring.json",
    "a60-v4-1-tool-compat-container/lint-delta.json",
    "a60-v4-1-tool-compat-container/review.json",
    "a60-v4-1-tool-compat-container/verification.json",
    "a60-v4-1-tool-compat-container/readiness.json",
    "a61-v4-1-file-live/pair.json",
    "a61-v4-1-file-live/diagnosis.json",
    "a62-transform-manifest/review.json",
    "a62-transform-manifest/verification.json",
    "a64-v4-2-transform-evidence-container/container-wiring.json",
    "a64-v4-2-transform-evidence-container/lint-delta.json",
    "a64-v4-2-transform-evidence-container/review.json",
    "a64-v4-2-transform-evidence-container/verification.json",
    "a64-v4-2-transform-evidence-container/readiness.json",
    "a65-deerflow-runtime-attribution-gate/readiness.json",
    "a65-deerflow-runtime-attribution-gate/review.json",
    "a66-deerflow-runtime-attribution-live/pair.json",
    "a66-deerflow-runtime-attribution-live/verification.json",
    "a67-v4-2-minibench16-live/failure-synthesis.json",
    "a67-v4-2-minibench16-live/review.json",
    "a67-v4-2-minibench16-live/verification.json",
    "a68-runtime-capabilities/review.json",
    "a68-runtime-capabilities/verification.json",
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
    v3_1_live = doc("a29-v3-1-live-canary/summary.json") if not missing else {}
    v3_2_gate = doc("a30-v3-2-gate/summary.json") if not missing else {}
    v3_2_conformance = doc("a31-v3-2-container-conformance/summary.json") if not missing else {}
    v3_2_wiring = documents.get("a31-v3-2-container-conformance/container-wiring.json", {})
    v3_2_live = doc("a32-v3-2-live-canary/summary.json") if not missing else {}
    v3_3_gate = doc("a33-v3-3-gate/summary.json") if not missing else {}
    v3_3_conformance = doc("a34-v3-3-container-conformance/summary.json") if not missing else {}
    v3_3_wiring = documents.get("a34-v3-3-container-conformance/container-wiring.json", {})
    v3_3_live = doc("a35-v3-3-live-canary/summary.json") if not missing else {}
    v3_4_gate = doc("a36-v3-4-thinking-max-gate/summary.json") if not missing else {}
    v3_4_conformance = doc("a37-v3-4-thinking-max-container/summary.json") if not missing else {}
    v3_4_wiring = documents.get("a37-v3-4-thinking-max-container/container-wiring.json", {})
    v3_4_live = doc("a38-v3-4-thinking-max-live/summary.json") if not missing else {}
    epoch_offline = doc("a39-mutation-epoch-offline/summary.json") if not missing else {}
    epoch_wiring = documents.get("a39-mutation-epoch-offline/container-wiring.json", {})
    v3_5_gate = doc("a40-v3-5-thinking-high-gate/summary.json") if not missing else {}
    v3_5_conformance = doc("a41-v3-5-thinking-high-container/summary.json") if not missing else {}
    v3_5_wiring = documents.get("a41-v3-5-thinking-high-container/container-wiring.json", {})
    v3_5_live = doc("a42-v3-5-thinking-high-live/summary.json") if not missing else {}
    v3_5_diagnosis = documents.get("a42-v3-5-thinking-high-live/diagnosis.json", {})
    grounding_gate = doc("a43-source-grounding-gate/summary.json") if not missing else {}
    grounding_conformance = doc("a44-source-grounding-container/summary.json") if not missing else {}
    grounding_wiring = documents.get("a44-source-grounding-container/container-wiring.json", {})
    v3_6_live = doc("a45-v3-6-live/summary.json") if not missing else {}
    v3_6_diagnosis = documents.get("a45-v3-6-live/diagnosis.json", {})
    v3_7_gate = doc("a46-v3-7-grounding-recovery-gate/summary.json") if not missing else {}
    v3_7_conformance = doc("a47-v3-7-grounding-recovery-container/summary.json") if not missing else {}
    v3_7_wiring = documents.get("a47-v3-7-grounding-recovery-container/container-wiring.json", {})
    v3_7_live = doc("a48-v3-7-live/summary.json") if not missing else {}
    v3_7_diagnosis = documents.get("a48-v3-7-live/diagnosis.json", {})
    v3_8_gate = doc("a49-v3-8-strict-delivery-gate/summary.json") if not missing else {}
    v3_8_conformance = doc("a50-v3-8-strict-delivery-container/summary.json") if not missing else {}
    v3_8_wiring = documents.get("a50-v3-8-strict-delivery-container/container-wiring.json", {})
    v3_8_live = doc("a51-v3-8-live/summary.json") if not missing else {}
    v3_8_diagnosis = documents.get("a51-v3-8-live/diagnosis.json", {})
    v3_9_gate = doc("a52-v3-9-artifact-repair-gate/summary.json") if not missing else {}
    v3_9_conformance = doc("a53-v3-9-artifact-repair-container/summary.json") if not missing else {}
    v3_9_wiring = documents.get("a53-v3-9-artifact-repair-container/container-wiring.json", {})
    diagnostic4 = doc("a54-diagnostic4-preflight/summary.json") if not missing else {}
    diagnostic4_lint_delta = documents.get(
        "a54-diagnostic4-preflight/lint-delta.json", {}
    )
    diagnostic4_readiness = documents.get(
        "a54-diagnostic4-preflight/readiness.json", {}
    )
    diagnostic4_live = doc("a55-diagnostic4-live/summary.json") if not missing else {}
    v4_gate = doc("a56-v4-cross-type-gate/summary.json") if not missing else {}
    v4_conformance = doc("a57-v4-cross-type-container/summary.json") if not missing else {}
    v4_wiring = documents.get("a57-v4-cross-type-container/container-wiring.json", {})
    v4_lint_delta = documents.get("a57-v4-cross-type-container/lint-delta.json", {})
    v4_readiness = documents.get("a57-v4-cross-type-container/readiness.json", {})
    v4_live = doc("a58-v4-file-live/summary.json") if not missing else {}
    v4_1_gate = doc("a59-v4-1-tool-compat-gate/summary.json") if not missing else {}
    v4_1_conformance = doc("a60-v4-1-tool-compat-container/summary.json") if not missing else {}
    v4_1_wiring = documents.get(
        "a60-v4-1-tool-compat-container/container-wiring.json", {}
    )
    v4_1_lint_delta = documents.get(
        "a60-v4-1-tool-compat-container/lint-delta.json", {}
    )
    v4_1_readiness = documents.get(
        "a60-v4-1-tool-compat-container/readiness.json", {}
    )
    v4_1_live = doc("a61-v4-1-file-live/summary.json") if not missing else {}
    transform_manifest = doc("a62-transform-manifest/summary.json") if not missing else {}
    v4_2_gate = doc("a63-v4-2-transform-evidence-gate/summary.json") if not missing else {}
    v4_2_conformance = doc("a64-v4-2-transform-evidence-container/summary.json") if not missing else {}
    v4_2_wiring = documents.get(
        "a64-v4-2-transform-evidence-container/container-wiring.json", {}
    )
    v4_2_lint_delta = documents.get(
        "a64-v4-2-transform-evidence-container/lint-delta.json", {}
    )
    v4_2_readiness = documents.get(
        "a64-v4-2-transform-evidence-container/readiness.json", {}
    )
    deerflow_attribution_gate = doc(
        "a65-deerflow-runtime-attribution-gate/summary.json"
    ) if not missing else {}
    deerflow_attribution_readiness = documents.get(
        "a65-deerflow-runtime-attribution-gate/readiness.json", {}
    )
    deerflow_attribution_live = doc(
        "a66-deerflow-runtime-attribution-live/summary.json"
    ) if not missing else {}
    v4_2_minibench_live = doc("a67-v4-2-minibench16-live/summary.json") if not missing else {}
    runtime_capabilities = doc("a68-runtime-capabilities/summary.json") if not missing else {}
    selected_live_row = next(
        (
            row
            for row in live_evolution.get("runs") or ()
            if row.get("run_id") == live_evolution.get("selected_candidate_run_id")
        ),
        None,
    )
    current_adaptive_source_sha256 = _adaptive_source_sha256()
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
        "v3_1_live_selected_candidate": v3_1_live.get("selected_candidate_variant"),
        "v3_1_live_paid_expansion_allowed": v3_1_live.get("paid_expansion_allowed"),
        "v3_1_live_row": next(
            (
                row
                for row in v3_1_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_1"
            ),
            None,
        ),
        "v3_2_gate_passed": v3_2_gate.get("passed"),
        "v3_2_single_canary_allowed": v3_2_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_2_paid_expansion_allowed": v3_2_gate.get("paid_expansion_allowed"),
        "v3_2_candidate_variant": (
            (v3_2_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_2_paid_canary_allowed": (
            v3_2_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_2_source_hash_matches": bool(v3_2_gate.get("adaptive_source_sha256"))
        and v3_2_gate.get("adaptive_source_sha256")
        == v3_2_wiring.get("adaptive_source_sha256"),
        "v3_2_profile_fingerprint_matches": bool(
            v3_2_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_2_gate.get("executable_policy_profile_fingerprint")
        == v3_2_wiring.get("executable_policy_profile_fingerprint"),
        "v3_2_live_selected_candidate": v3_2_live.get("selected_candidate_variant"),
        "v3_2_live_paid_expansion_allowed": v3_2_live.get("paid_expansion_allowed"),
        "v3_2_live_row": next(
            (
                row
                for row in v3_2_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_2"
            ),
            None,
        ),
        "v3_3_gate_passed": v3_3_gate.get("passed"),
        "v3_3_single_canary_allowed": v3_3_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_3_paid_expansion_allowed": v3_3_gate.get("paid_expansion_allowed"),
        "v3_3_non_vacuity_present": (
            v3_3_gate.get("contract") or {}
        ).get("artifact_non_vacuity_constraint_present"),
        "v3_3_historical_artifact_diagnostics": (
            v3_3_gate.get("historical_artifact") or {}
        ).get("diagnostic_types"),
        "v3_3_runtime_limit_response_rejected": (
            v3_3_gate.get("runtime_semantics") or {}
        ).get("runtime_limit_response_rejected"),
        "v3_3_direct_script_transform": (
            v3_3_gate.get("runtime_semantics") or {}
        ).get("direct_public_script_is_transform"),
        "v3_3_candidate_variant": (
            (v3_3_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_3_paid_canary_allowed": (
            v3_3_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_3_source_hash_matches": bool(v3_3_gate.get("adaptive_source_sha256"))
        and v3_3_gate.get("adaptive_source_sha256")
        == v3_3_wiring.get("adaptive_source_sha256"),
        "v3_3_profile_fingerprint_matches": bool(
            v3_3_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_3_gate.get("executable_policy_profile_fingerprint")
        == v3_3_wiring.get("executable_policy_profile_fingerprint"),
        "v3_3_live_selected_candidate": v3_3_live.get("selected_candidate_variant"),
        "v3_3_live_paid_expansion_allowed": v3_3_live.get("paid_expansion_allowed"),
        "v3_3_live_row": next(
            (
                row
                for row in v3_3_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_3"
            ),
            None,
        ),
        "v3_4_gate_passed": v3_4_gate.get("passed"),
        "v3_4_single_canary_allowed": v3_4_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_4_paid_expansion_allowed": v3_4_gate.get("paid_expansion_allowed"),
        "v3_4_candidate_variant": (
            (v3_4_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_4_paid_canary_allowed": (
            v3_4_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_4_source_hash_matches": bool(v3_4_gate.get("adaptive_source_sha256"))
        and v3_4_gate.get("adaptive_source_sha256")
        == v3_4_wiring.get("adaptive_source_sha256"),
        "v3_4_profile_fingerprint_matches": bool(
            v3_4_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_4_gate.get("executable_policy_profile_fingerprint")
        == v3_4_wiring.get("executable_policy_profile_fingerprint"),
        "v3_4_thinking_max_configured": (
            v3_4_wiring.get("checks") or {}
        ).get("thinking_max_request_configured"),
        "v3_4_completion_conjunction_verified": (
            v3_4_wiring.get("checks") or {}
        ).get("completion_conjunction_preserves_assessments"),
        "v3_4_live_selected_candidate": v3_4_live.get("selected_candidate_variant"),
        "v3_4_live_paid_expansion_allowed": v3_4_live.get("paid_expansion_allowed"),
        "v3_4_live_row": next(
            (
                row
                for row in v3_4_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_4"
            ),
            None,
        ),
        "mutation_epoch_offline_gate_passed": epoch_offline.get("zero_model_passed"),
        "mutation_epoch_object_context_monotonic": (
            epoch_wiring.get("checks") or {}
        ).get("object_context_epoch_monotonic"),
        "v3_5_gate_passed": v3_5_gate.get("passed"),
        "v3_5_single_canary_allowed": v3_5_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_5_paid_expansion_allowed": v3_5_gate.get("paid_expansion_allowed"),
        "v3_5_candidate_variant": (
            (v3_5_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_5_paid_canary_allowed": (
            v3_5_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_5_source_hash_matches": bool(v3_5_gate.get("adaptive_source_sha256"))
        and v3_5_gate.get("adaptive_source_sha256")
        == v3_5_wiring.get("adaptive_source_sha256"),
        "v3_5_profile_fingerprint_matches": bool(
            v3_5_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_5_gate.get("executable_policy_profile_fingerprint")
        == v3_5_wiring.get("executable_policy_profile_fingerprint"),
        "v3_5_thinking_effort": v3_5_wiring.get("thinking_request_effort"),
        "v3_5_thinking_request_configured": (
            v3_5_wiring.get("checks") or {}
        ).get("thinking_request_configured"),
        "v3_5_epoch_monotonic": (
            v3_5_wiring.get("checks") or {}
        ).get("object_context_epoch_monotonic"),
        "v3_5_live_selected_candidate": v3_5_live.get("selected_candidate_variant"),
        "v3_5_live_paid_expansion_allowed": v3_5_live.get("paid_expansion_allowed"),
        "v3_5_live_row": next(
            (
                row
                for row in v3_5_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_5"
            ),
            None,
        ),
        "v3_5_diagnosis_row": next(
            (
                row
                for row in v3_5_diagnosis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_evidence_workspace_v3_5"
            ),
            None,
        ),
        "source_grounding_gate_passed": grounding_gate.get("passed"),
        "source_grounding_single_canary_allowed": grounding_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "source_grounding_paid_expansion_allowed": grounding_gate.get(
            "paid_expansion_allowed"
        ),
        "source_grounding_contract_present": (
            grounding_gate.get("contract") or {}
        ).get("artifact_grounding_criterion_present"),
        "source_grounding_historical_copy_rejected": (
            grounding_gate.get("historical_artifact") or {}
        ).get("grounding_rejected"),
        "source_grounding_candidate_variant": (
            (grounding_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "source_grounding_paid_canary_allowed": (
            grounding_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "source_grounding_source_hash_matches": bool(
            grounding_gate.get("adaptive_source_sha256")
        )
        and grounding_gate.get("adaptive_source_sha256")
        == grounding_wiring.get("adaptive_source_sha256"),
        "source_grounding_profile_fingerprint_matches": bool(
            grounding_gate.get("executable_policy_profile_fingerprint")
        )
        and grounding_gate.get("executable_policy_profile_fingerprint")
        == grounding_wiring.get("executable_policy_profile_fingerprint"),
        "source_grounding_container_copy_guard": (
            grounding_wiring.get("checks") or {}
        ).get("provisional_copy_guard_enforced"),
        "v3_6_live_selected_candidate": v3_6_live.get("selected_candidate_variant"),
        "v3_6_live_paid_expansion_allowed": v3_6_live.get("paid_expansion_allowed"),
        "v3_6_live_row": next(
            (
                row
                for row in v3_6_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_6"
            ),
            None,
        ),
        "v3_6_diagnosis_row": next(
            (
                row
                for row in v3_6_diagnosis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_6"
            ),
            None,
        ),
        "v3_7_gate_passed": v3_7_gate.get("passed"),
        "v3_7_single_canary_allowed": v3_7_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_7_paid_expansion_allowed": v3_7_gate.get("paid_expansion_allowed"),
        "v3_7_candidate_variant": (
            (v3_7_conformance.get("profiles") or {}).get("candidate") or {}
        ).get("name"),
        "v3_7_paid_canary_allowed": (
            v3_7_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_7_source_hash_matches": bool(v3_7_gate.get("adaptive_source_sha256"))
        and v3_7_gate.get("adaptive_source_sha256")
        == v3_7_wiring.get("adaptive_source_sha256"),
        "v3_7_profile_fingerprint_matches": bool(
            v3_7_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_7_gate.get("executable_policy_profile_fingerprint")
        == v3_7_wiring.get("executable_policy_profile_fingerprint"),
        "v3_7_blocked_grounding_preserves_artifact_recovery": (
            v3_7_wiring.get("checks") or {}
        ).get("blocked_grounding_preserves_artifact_recovery"),
        "v3_7_live_selected_candidate": v3_7_live.get("selected_candidate_variant"),
        "v3_7_live_paid_expansion_allowed": v3_7_live.get("paid_expansion_allowed"),
        "v3_7_live_row": next(
            (
                row
                for row in v3_7_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_7"
            ),
            None,
        ),
        "v3_7_diagnosis_row": next(
            (
                row
                for row in v3_7_diagnosis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_7"
            ),
            None,
        ),
        "v3_8_gate_passed": v3_8_gate.get("passed"),
        "v3_8_single_canary_allowed": v3_8_gate.get("candidate_single_development_canary_allowed"),
        "v3_8_paid_expansion_allowed": v3_8_gate.get("paid_expansion_allowed"),
        "v3_8_candidate_variant": (((v3_8_conformance.get("profiles") or {}).get("candidate") or {}).get("name")),
        "v3_8_paid_canary_allowed": (v3_8_conformance.get("paid_candidate_canary") or {}).get("allowed"),
        "v3_8_source_hash_matches": bool(v3_8_gate.get("adaptive_source_sha256"))
        and v3_8_gate.get("adaptive_source_sha256")
        == v3_8_wiring.get("adaptive_source_sha256"),
        "v3_8_profile_fingerprint_matches": bool(
            v3_8_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_8_gate.get("executable_policy_profile_fingerprint")
        == v3_8_wiring.get("executable_policy_profile_fingerprint"),
        "v3_8_delivery_non_output_write_blocked": (
            v3_8_wiring.get("checks") or {}
        ).get("delivery_non_output_write_blocked"),
        "v3_8_live_selected_candidate": v3_8_live.get("selected_candidate_variant"),
        "v3_8_live_paid_expansion_allowed": v3_8_live.get("paid_expansion_allowed"),
        "v3_8_live_row": next(
            (
                row
                for row in v3_8_live.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_8"
            ),
            None,
        ),
        "v3_8_diagnosis_row": next(
            (
                row
                for row in v3_8_diagnosis.get("runs") or ()
                if row.get("variant") == "adaptive_harness_source_grounding_v3_8"
            ),
            None,
        ),
        "v3_9_gate_passed": v3_9_gate.get("passed"),
        "v3_9_single_canary_allowed": v3_9_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v3_9_paid_expansion_allowed": v3_9_gate.get("paid_expansion_allowed"),
        "v3_9_candidate_variant": (
            ((v3_9_conformance.get("profiles") or {}).get("candidate") or {}).get("name")
        ),
        "v3_9_paid_canary_allowed": (
            v3_9_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v3_9_source_hash_matches": bool(v3_9_gate.get("adaptive_source_sha256"))
        and v3_9_gate.get("adaptive_source_sha256")
        == v3_9_wiring.get("adaptive_source_sha256"),
        "v3_9_profile_fingerprint_matches": bool(
            v3_9_gate.get("executable_policy_profile_fingerprint")
        )
        and v3_9_gate.get("executable_policy_profile_fingerprint")
        == v3_9_wiring.get("executable_policy_profile_fingerprint"),
        "v3_9_delivery_rearmed_after_invalid_artifact": (
            v3_9_wiring.get("checks") or {}
        ).get("delivery_rearmed_after_invalid_artifact"),
        "v3_9_invalid_artifact_recovery_rewrites_output": (
            v3_9_wiring.get("checks") or {}
        ).get("invalid_artifact_recovery_rewrites_output"),
        "diagnostic4_passed": diagnostic4.get("passed"),
        "diagnostic4_task_ids": diagnostic4.get("task_ids"),
        "diagnostic4_counts": diagnostic4.get("counts"),
        "diagnostic4_optimization_dimensions": diagnostic4.get(
            "optimization_dimensions"
        ),
        "diagnostic4_thinking_high_default": (
            diagnostic4.get("checks") or {}
        ).get("thinking_high_default"),
        "diagnostic4_minibench16_disjoint": (
            diagnostic4.get("checks") or {}
        ).get("minibench16_disjoint"),
        "diagnostic4_paid_expansion_allowed": diagnostic4.get(
            "paid_expansion_allowed"
        ),
        "diagnostic4_new_lint_findings": diagnostic4_lint_delta.get(
            "new_finding_count"
        ),
        "diagnostic4_readiness": diagnostic4_readiness.get("ready"),
        "diagnostic4_live_run_count": diagnostic4_live.get("run_count"),
        "diagnostic4_live_category_counts": diagnostic4_live.get("category_counts"),
        "diagnostic4_live_all_integrity_passed": diagnostic4_live.get(
            "all_integrity_passed"
        ),
        "diagnostic4_live_passed_runs": diagnostic4_live.get("passed_runs"),
        "diagnostic4_live_capacity_score_mean": diagnostic4_live.get(
            "capacity_score_mean"
        ),
        "diagnostic4_live_cost": diagnostic4_live.get("cost"),
        "diagnostic4_live_cross_type_signals": diagnostic4_live.get(
            "cross_type_signals"
        ),
        "diagnostic4_live_paid_expansion_allowed": diagnostic4_live.get(
            "paid_expansion_allowed"
        ),
        "v4_gate_passed": v4_gate.get("passed"),
        "v4_single_canary_allowed": v4_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v4_paid_expansion_allowed": v4_gate.get("paid_expansion_allowed"),
        "v4_candidate_variant": (
            ((v4_conformance.get("profiles") or {}).get("candidate") or {}).get("name")
        ),
        "v4_paid_canary_allowed": (
            v4_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v4_source_hash_matches": bool(v4_gate.get("adaptive_source_sha256"))
        and v4_gate.get("adaptive_source_sha256")
        == v4_wiring.get("adaptive_source_sha256"),
        "v4_profile_fingerprint_matches": bool(
            v4_gate.get("executable_policy_profile_fingerprint")
        )
        and v4_gate.get("executable_policy_profile_fingerprint")
        == v4_wiring.get("executable_policy_profile_fingerprint"),
        "v4_required_artifact_target_enforced": (
            v4_wiring.get("checks") or {}
        ).get("required_artifact_target_enforced"),
        "v4_required_directory_artifact_observed": (
            v4_wiring.get("checks") or {}
        ).get("required_directory_artifact_observed"),
        "v4_turn_observation_oserror_fails_closed": (
            v4_wiring.get("checks") or {}
        ).get("turn_observation_oserror_fails_closed"),
        "v4_delivery_violation_budget_enforced": (
            v4_wiring.get("checks") or {}
        ).get("delivery_violation_budget_enforced"),
        "v4_new_full_repo_lint_findings": v4_lint_delta.get("new_finding_count"),
        "v4_readiness": v4_readiness.get("ready"),
        "v4_live_baseline": v4_live.get("baseline"),
        "v4_live_candidate": v4_live.get("candidate"),
        "v4_live_correct_required_target_attempted": v4_live.get(
            "correct_required_target_attempted"
        ),
        "v4_live_correct_write_failed_missing_description": v4_live.get(
            "correct_write_failed_missing_description"
        ),
        "v4_live_repeated_delivery_early_end_results": v4_live.get(
            "repeated_delivery_early_end_results"
        ),
        "v4_live_selected_candidate": v4_live.get("selected_candidate_variant"),
        "v4_live_paid_expansion_allowed": v4_live.get("paid_expansion_allowed"),
        "v4_1_gate_passed": v4_1_gate.get("passed"),
        "v4_1_single_canary_allowed": v4_1_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v4_1_paid_expansion_allowed": v4_1_gate.get("paid_expansion_allowed"),
        "v4_1_candidate_variant": (
            ((v4_1_conformance.get("profiles") or {}).get("candidate") or {}).get("name")
        ),
        "v4_1_paid_canary_allowed": (
            v4_1_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v4_1_source_hash_matches": bool(v4_1_gate.get("adaptive_source_sha256"))
        and v4_1_gate.get("adaptive_source_sha256")
        == v4_1_wiring.get("adaptive_source_sha256"),
        "v4_1_profile_fingerprint_matches": bool(
            v4_1_gate.get("executable_policy_profile_fingerprint")
        )
        and v4_1_gate.get("executable_policy_profile_fingerprint")
        == v4_1_wiring.get("executable_policy_profile_fingerprint"),
        "v4_1_delivery_batch_guard_enforced": (
            v4_1_wiring.get("checks") or {}
        ).get("delivery_batch_guard_enforced"),
        "v4_1_write_description_repair_enforced": (
            v4_1_wiring.get("checks") or {}
        ).get("write_description_repair_enforced"),
        "v4_1_new_full_repo_lint_findings": v4_1_lint_delta.get(
            "new_finding_count"
        ),
        "v4_1_readiness": v4_1_readiness.get("ready"),
        "v4_1_live_baseline": v4_1_live.get("baseline"),
        "v4_1_live_candidate": v4_1_live.get("candidate"),
        "v4_1_live_cost": v4_1_live.get("cost"),
        "v4_1_live_delivery_batch_guard_result_count": v4_1_live.get(
            "delivery_batch_guard_result_count"
        ),
        "v4_1_live_compatibility_repairs_applied": v4_1_live.get(
            "compatibility_repairs_applied"
        ),
        "v4_1_live_task_transform_missing_snapshot_error": v4_1_live.get(
            "task_transform_missing_snapshot_error"
        ),
        "v4_1_live_unsafe_path_attempt_blocked": v4_1_live.get(
            "unsafe_path_attempt_blocked"
        ),
        "v4_1_live_selected_candidate": v4_1_live.get(
            "selected_candidate_variant"
        ),
        "v4_1_live_paid_expansion_allowed": v4_1_live.get(
            "paid_expansion_allowed"
        ),
        "transform_manifest_model_calls": transform_manifest.get("model_calls"),
        "transform_manifest_execution_performed": transform_manifest.get(
            "execution_performed"
        ),
        "transform_manifest_missing_read_count": transform_manifest.get(
            "missing_read_count"
        ),
        "transform_manifest_row": next(
            iter(transform_manifest.get("manifests") or ()),
            None,
        ),
        "transform_manifest_paid_expansion_allowed": transform_manifest.get(
            "paid_expansion_allowed"
        ),
        "v4_2_gate_passed": v4_2_gate.get("passed"),
        "v4_2_single_canary_allowed": v4_2_gate.get(
            "candidate_single_development_canary_allowed"
        ),
        "v4_2_paid_expansion_allowed": v4_2_gate.get("paid_expansion_allowed"),
        "v4_2_candidate_variant": (
            ((v4_2_conformance.get("profiles") or {}).get("candidate") or {}).get("name")
        ),
        "v4_2_paid_canary_allowed": (
            v4_2_conformance.get("paid_candidate_canary") or {}
        ).get("allowed"),
        "v4_2_source_hash_matches": bool(v4_2_gate.get("adaptive_source_sha256"))
        and v4_2_gate.get("adaptive_source_sha256")
        == v4_2_wiring.get("adaptive_source_sha256")
        == current_adaptive_source_sha256,
        "current_adaptive_source_sha256": current_adaptive_source_sha256,
        "v4_2_profile_fingerprint_matches": bool(
            v4_2_gate.get("executable_policy_profile_fingerprint")
        )
        and v4_2_gate.get("executable_policy_profile_fingerprint")
        == v4_2_wiring.get("executable_policy_profile_fingerprint"),
        "v4_2_transform_manifest_evidence_wired": (
            v4_2_wiring.get("checks") or {}
        ).get("transform_manifest_evidence_wired"),
        "v4_2_production_transform_manifest_bridge_wired": (
            v4_2_wiring.get("checks") or {}
        ).get("production_transform_manifest_bridge_wired"),
        "v4_2_new_full_repo_lint_findings": v4_2_lint_delta.get(
            "new_finding_count"
        ),
        "v4_2_readiness": v4_2_readiness.get("ready"),
        "deerflow_attribution_gate_passed": deerflow_attribution_gate.get("passed"),
        "deerflow_attribution_control_fields": deerflow_attribution_gate.get(
            "controlled_fields"
        ),
        "deerflow_attribution_allowed_runs": deerflow_attribution_gate.get(
            "allowed_runs"
        ),
        "deerflow_historical_vanilla": deerflow_attribution_gate.get(
            "historical_vanilla"
        ),
        "deerflow_attribution_readiness": deerflow_attribution_readiness.get("ready"),
        "deerflow_attribution_vanilla": deerflow_attribution_live.get("vanilla"),
        "deerflow_attribution_adaptive": deerflow_attribution_live.get("adaptive"),
        "deerflow_attribution_cost": deerflow_attribution_live.get("cost"),
        "deerflow_attribution_pair_valid": deerflow_attribution_live.get("pair_valid"),
        "deerflow_attribution_model_visible_surface": deerflow_attribution_live.get(
            "model_visible_surface"
        ),
        "deerflow_attribution_both_failed": deerflow_attribution_live.get("both_failed"),
        "deerflow_attribution_vanilla_no_final_response": deerflow_attribution_live.get(
            "vanilla_no_final_response"
        ),
        "deerflow_attribution_vanilla_sandbox_false_positive_count": (
            deerflow_attribution_live.get("vanilla_sandbox_false_positive_count")
        ),
        "deerflow_attribution_result": deerflow_attribution_live.get("attribution"),
        "deerflow_attribution_decision": deerflow_attribution_live.get("decision"),
        "deerflow_attribution_paid_expansion_allowed": deerflow_attribution_live.get(
            "paid_expansion_allowed"
        ),
        "v4_2_minibench_task_count": v4_2_minibench_live.get("task_count"),
        "v4_2_minibench_passed": v4_2_minibench_live.get("passed"),
        "v4_2_minibench_capacity_score_mean": v4_2_minibench_live.get(
            "capacity_score_mean"
        ),
        "v4_2_minibench_total_tokens": v4_2_minibench_live.get("total_tokens"),
        "v4_2_minibench_tool_calls": v4_2_minibench_live.get("tool_calls"),
        "v4_2_minibench_elapsed_sec": v4_2_minibench_live.get("elapsed_sec"),
        "v4_2_minibench_output_file_count": v4_2_minibench_live.get(
            "output_file_count"
        ),
        "v4_2_minibench_integrity_passed_tasks": v4_2_minibench_live.get(
            "integrity_passed_tasks"
        ),
        "v4_2_minibench_partitions": v4_2_minibench_live.get("partitions"),
        "v4_2_minibench_partial_quality_tasks": v4_2_minibench_live.get(
            "partial_quality_tasks"
        ),
        "v4_2_minibench_failure_synthesis": v4_2_minibench_live.get(
            "failure_synthesis"
        ),
        "v4_2_minibench_decision": v4_2_minibench_live.get("decision"),
        "v4_2_minibench_paid_expansion_allowed": v4_2_minibench_live.get(
            "paid_expansion_allowed"
        ),
        "runtime_capability_profiles": runtime_capabilities.get("profiles"),
        "runtime_capability_profile_fingerprints": runtime_capabilities.get(
            "profile_fingerprints"
        ),
        "runtime_capability_decisions": runtime_capabilities.get("decisions"),
        "runtime_capability_invariants": runtime_capabilities.get("invariants"),
        "runtime_capability_execution_performed": runtime_capabilities.get(
            "runtime_execution_performed"
        ),
        "runtime_capability_paid_expansion_allowed": runtime_capabilities.get(
            "paid_expansion_allowed"
        ),
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
        "v3_1_regression_not_promoted": (
            (claims["v3_1_live_row"] or {}).get("passed") is False
            and (claims["v3_1_live_row"] or {}).get("capacity_score") == 0.0
            and (claims["v3_1_live_row"] or {}).get("output_file_count") == 0
            and claims["v3_1_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_1_live_paid_expansion_allowed"] is False
        ),
        "v3_2_gate_is_single_canary_only": claims["v3_2_gate_passed"] is True
        and claims["v3_2_single_canary_allowed"] is True
        and claims["v3_2_paid_expansion_allowed"] is False,
        "v3_2_container_gate_cleared": claims["v3_2_candidate_variant"]
        == "adaptive_harness_evidence_workspace_v3_2"
        and claims["v3_2_paid_canary_allowed"] is True
        and claims["v3_2_source_hash_matches"] is True
        and claims["v3_2_profile_fingerprint_matches"] is True,
        "v3_2_regression_not_promoted": (
            (claims["v3_2_live_row"] or {}).get("passed") is False
            and (claims["v3_2_live_row"] or {}).get("capacity_score") == 0.2
            and (claims["v3_2_live_row"] or {}).get("output_file_count") == 1
            and claims["v3_2_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_2_live_paid_expansion_allowed"] is False
        ),
        "v3_3_gate_is_single_canary_only": claims["v3_3_gate_passed"] is True
        and claims["v3_3_single_canary_allowed"] is True
        and claims["v3_3_paid_expansion_allowed"] is False,
        "v3_3_public_completion_regressions_closed": claims["v3_3_non_vacuity_present"]
        is True
        and claims["v3_3_historical_artifact_diagnostics"] == ["all_collections_empty"]
        and claims["v3_3_runtime_limit_response_rejected"] is True
        and claims["v3_3_direct_script_transform"] is True,
        "v3_3_container_gate_cleared": claims["v3_3_candidate_variant"]
        == "adaptive_harness_evidence_workspace_v3_3"
        and claims["v3_3_paid_canary_allowed"] is True
        and claims["v3_3_source_hash_matches"] is True
        and claims["v3_3_profile_fingerprint_matches"] is True,
        "v3_3_regression_not_promoted": (
            (claims["v3_3_live_row"] or {}).get("passed") is False
            and (claims["v3_3_live_row"] or {}).get("capacity_score") == 0.0
            and (claims["v3_3_live_row"] or {}).get("output_file_count") == 0
            and claims["v3_3_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_3_live_paid_expansion_allowed"] is False
        ),
        "v3_4_gate_is_single_canary_only": claims["v3_4_gate_passed"] is True
        and claims["v3_4_single_canary_allowed"] is True
        and claims["v3_4_paid_expansion_allowed"] is False,
        "v3_4_container_gate_cleared": claims["v3_4_candidate_variant"]
        == "adaptive_harness_evidence_workspace_v3_4"
        and claims["v3_4_paid_canary_allowed"] is True
        and claims["v3_4_source_hash_matches"] is True
        and claims["v3_4_profile_fingerprint_matches"] is True
        and claims["v3_4_thinking_max_configured"] is True
        and claims["v3_4_completion_conjunction_verified"] is True,
        "v3_4_thinking_max_regression_not_promoted": (
            (claims["v3_4_live_row"] or {}).get("passed") is False
            and (claims["v3_4_live_row"] or {}).get("capacity_score") == 0.2
            and (claims["v3_4_live_row"] or {}).get("total_tokens") == 719574
            and (claims["v3_4_live_row"] or {}).get("output_file_count") == 1
            and claims["v3_4_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_4_live_paid_expansion_allowed"] is False
        ),
        "mutation_epoch_offline_fix_verified": claims[
            "mutation_epoch_offline_gate_passed"
        ]
        is True
        and claims["mutation_epoch_object_context_monotonic"] is True,
        "v3_5_gate_is_single_canary_only": claims["v3_5_gate_passed"] is True
        and claims["v3_5_single_canary_allowed"] is True
        and claims["v3_5_paid_expansion_allowed"] is False,
        "v3_5_high_container_gate_cleared": claims["v3_5_candidate_variant"]
        == "adaptive_harness_evidence_workspace_v3_5"
        and claims["v3_5_paid_canary_allowed"] is True
        and claims["v3_5_source_hash_matches"] is True
        and claims["v3_5_profile_fingerprint_matches"] is True
        and claims["v3_5_thinking_effort"] == "high"
        and claims["v3_5_thinking_request_configured"] is True
        and claims["v3_5_epoch_monotonic"] is True,
        "v3_5_high_result_not_promoted": (
            (claims["v3_5_live_row"] or {}).get("passed") is False
            and (claims["v3_5_live_row"] or {}).get("capacity_score") == 0.4
            and (claims["v3_5_live_row"] or {}).get("total_tokens") == 765768
            and (claims["v3_5_live_row"] or {}).get("output_file_count") == 1
            and ((claims["v3_5_diagnosis_row"] or {}).get("signals") or {}).get(
                "mutation_epoch_regression"
            )
            is False
            and claims["v3_5_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_5_live_paid_expansion_allowed"] is False
        ),
        "source_grounding_zero_model_gate_cleared": claims[
            "source_grounding_gate_passed"
        ]
        is True
        and claims["source_grounding_single_canary_allowed"] is True
        and claims["source_grounding_paid_expansion_allowed"] is False
        and claims["source_grounding_contract_present"] is True
        and claims["source_grounding_historical_copy_rejected"] is True,
        "source_grounding_container_gate_cleared": claims[
            "source_grounding_candidate_variant"
        ]
        == "adaptive_harness_source_grounding_v3_6"
        and claims["source_grounding_paid_canary_allowed"] is True
        and claims["source_grounding_source_hash_matches"] is True
        and claims["source_grounding_profile_fingerprint_matches"] is True
        and claims["source_grounding_container_copy_guard"] is True,
        "v3_6_regression_not_promoted": (
            (claims["v3_6_live_row"] or {}).get("passed") is False
            and (claims["v3_6_live_row"] or {}).get("capacity_score") == 0.0
            and (claims["v3_6_live_row"] or {}).get("total_tokens") == 320755
            and (claims["v3_6_live_row"] or {}).get("output_file_count") == 0
            and ((claims["v3_6_diagnosis_row"] or {}).get("signals") or {}).get(
                "mutation_epoch_regression"
            )
            is False
            and claims["v3_6_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_6_live_paid_expansion_allowed"] is False
        ),
        "v3_7_gate_is_single_canary_only": claims["v3_7_gate_passed"] is True
        and claims["v3_7_single_canary_allowed"] is True
        and claims["v3_7_paid_expansion_allowed"] is False,
        "v3_7_container_gate_cleared": claims["v3_7_candidate_variant"]
        == "adaptive_harness_source_grounding_v3_7"
        and claims["v3_7_paid_canary_allowed"] is True
        and claims["v3_7_source_hash_matches"] is True
        and claims["v3_7_profile_fingerprint_matches"] is True
        and claims["v3_7_blocked_grounding_preserves_artifact_recovery"] is True,
        "v3_7_regression_not_promoted": (
            (claims["v3_7_live_row"] or {}).get("passed") is False
            and (claims["v3_7_live_row"] or {}).get("capacity_score") == 0.0
            and (claims["v3_7_live_row"] or {}).get("total_tokens") == 439869
            and (claims["v3_7_live_row"] or {}).get("output_file_count") == 0
            and ((claims["v3_7_diagnosis_row"] or {}).get("signals") or {}).get("mutation_epoch_regression") is False
            and claims["v3_7_live_selected_candidate"] == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_7_live_paid_expansion_allowed"] is False
        ),
        "v3_8_gate_is_single_canary_only": claims["v3_8_gate_passed"] is True
        and claims["v3_8_single_canary_allowed"] is True
        and claims["v3_8_paid_expansion_allowed"] is False,
        "v3_8_container_gate_cleared": claims["v3_8_candidate_variant"]
        == "adaptive_harness_source_grounding_v3_8"
        and claims["v3_8_paid_canary_allowed"] is True
        and claims["v3_8_source_hash_matches"] is True
        and claims["v3_8_profile_fingerprint_matches"] is True
        and claims["v3_8_delivery_non_output_write_blocked"] is True,
        "v3_8_regression_not_promoted": (
            (claims["v3_8_live_row"] or {}).get("passed") is False
            and (claims["v3_8_live_row"] or {}).get("capacity_score") == 0.2
            and (claims["v3_8_live_row"] or {}).get("total_tokens") == 858909
            and (claims["v3_8_live_row"] or {}).get("output_file_count") == 1
            and ((claims["v3_8_diagnosis_row"] or {}).get("signals") or {}).get(
                "mutation_epoch_regression"
            )
            is False
            and claims["v3_8_live_selected_candidate"]
            == "adaptive_harness_runtime_evolution_v2_6"
            and claims["v3_8_live_paid_expansion_allowed"] is False
        ),
        "v3_9_gate_is_single_canary_only": claims["v3_9_gate_passed"] is True
        and claims["v3_9_single_canary_allowed"] is True
        and claims["v3_9_paid_expansion_allowed"] is False,
        "v3_9_container_gate_cleared": claims["v3_9_candidate_variant"]
        == "adaptive_harness_source_grounding_v3_9"
        and claims["v3_9_paid_canary_allowed"] is True
        and claims["v3_9_source_hash_matches"] is True
        and claims["v3_9_profile_fingerprint_matches"] is True
        and claims["v3_9_delivery_rearmed_after_invalid_artifact"] is True
        and claims["v3_9_invalid_artifact_recovery_rewrites_output"] is True,
        "diagnostic4_is_breadth_first_and_isolated": claims["diagnostic4_passed"]
        is True
        and len(claims["diagnostic4_task_ids"] or ()) == 4
        and ((claims["diagnostic4_counts"] or {}).get("category") or {})
        == {"api": 1, "browser": 1, "cli": 1, "file": 1}
        and ((claims["diagnostic4_counts"] or {}).get("difficulty_band") or {})
        == {"easy": 2, "medium": 2}
        and claims["diagnostic4_thinking_high_default"] is True
        and claims["diagnostic4_minibench16_disjoint"] is True
        and len(claims["diagnostic4_optimization_dimensions"] or ()) == 10
        and claims["diagnostic4_new_lint_findings"] == 0
        and claims["diagnostic4_readiness"] is True
        and claims["diagnostic4_paid_expansion_allowed"] is False,
        "diagnostic4_live_is_cross_type_failure_evidence_only": (
            claims["diagnostic4_live_run_count"] == 4
            and claims["diagnostic4_live_category_counts"]
            == {"api": 1, "browser": 1, "cli": 1, "file": 1}
            and claims["diagnostic4_live_all_integrity_passed"] is True
            and claims["diagnostic4_live_passed_runs"] == 0
            and (claims["diagnostic4_live_cost"] or {}).get("total_tokens") == 1206127
            and claims["diagnostic4_live_paid_expansion_allowed"] is False
        ),
        "v4_gate_is_single_canary_only": claims["v4_gate_passed"] is True
        and claims["v4_single_canary_allowed"] is True
        and claims["v4_paid_expansion_allowed"] is False,
        "v4_container_gate_cleared": claims["v4_candidate_variant"]
        == "adaptive_harness_cross_type_artifact_v4_0"
        and claims["v4_paid_canary_allowed"] is True
        and claims["v4_source_hash_matches"] is True
        and claims["v4_profile_fingerprint_matches"] is True
        and claims["v4_required_artifact_target_enforced"] is True
        and claims["v4_required_directory_artifact_observed"] is True
        and claims["v4_turn_observation_oserror_fails_closed"] is True
        and claims["v4_delivery_violation_budget_enforced"] is True
        and claims["v4_new_full_repo_lint_findings"] == 0
        and claims["v4_readiness"] is True,
        "v4_live_regression_not_promoted": (
            (claims["v4_live_candidate"] or {}).get("passed") is False
            and (claims["v4_live_candidate"] or {}).get("capacity_score") == 0.0
            and (claims["v4_live_candidate"] or {}).get("total_tokens") == 337994
            and claims["v4_live_correct_required_target_attempted"] is True
            and claims["v4_live_correct_write_failed_missing_description"] is True
            and claims["v4_live_repeated_delivery_early_end_results"] == 15
            and claims["v4_live_selected_candidate"]
            == "adaptive_harness_source_grounding_v3_9"
            and claims["v4_live_paid_expansion_allowed"] is False
        ),
        "v4_1_gate_is_single_canary_only": claims["v4_1_gate_passed"] is True
        and claims["v4_1_single_canary_allowed"] is True
        and claims["v4_1_paid_expansion_allowed"] is False,
        "v4_1_container_gate_cleared": claims["v4_1_candidate_variant"]
        == "adaptive_harness_cross_type_artifact_v4_1"
        and claims["v4_1_paid_canary_allowed"] is True
        and claims["v4_1_source_hash_matches"] is True
        and claims["v4_1_profile_fingerprint_matches"] is True
        and claims["v4_1_delivery_batch_guard_enforced"] is True
        and claims["v4_1_write_description_repair_enforced"] is True
        and claims["v4_1_new_full_repo_lint_findings"] == 0
        and claims["v4_1_readiness"] is True,
        "v4_1_cost_improvement_without_quality_promotion": (
            (claims["v4_1_live_candidate"] or {}).get("passed") is False
            and (claims["v4_1_live_candidate"] or {}).get("capacity_score") == 0.0
            and (claims["v4_1_live_candidate"] or {}).get("total_tokens") == 220496
            and (claims["v4_1_live_candidate"] or {}).get("tool_calls") == 31
            and (claims["v4_1_live_cost"] or {}).get("token_delta") == -117498
            and (claims["v4_1_live_cost"] or {}).get("tool_call_delta") == -14
            and claims["v4_1_live_delivery_batch_guard_result_count"] == 2
            and claims["v4_1_live_task_transform_missing_snapshot_error"] is True
            and claims["v4_1_live_unsafe_path_attempt_blocked"] is True
            and claims["v4_1_live_selected_candidate"]
            == "adaptive_harness_cross_type_artifact_v4_1"
            and claims["v4_1_live_paid_expansion_allowed"] is False
        ),
        "transform_manifest_detects_a61_precondition_offline": (
            claims["transform_manifest_model_calls"] == 0
            and claims["transform_manifest_execution_performed"] is False
            and claims["transform_manifest_missing_read_count"] == 1
            and (claims["transform_manifest_row"] or {}).get("script_path")
            == "workspace/analysis/audit.py"
            and any(
                access.get("kind") == "read"
                and access.get("path") == "snapshots/manifest.json"
                and access.get("exists") is False
                for access in (claims["transform_manifest_row"] or {}).get("accesses") or ()
            )
            and claims["transform_manifest_paid_expansion_allowed"] is False
        ),
        "v4_2_gate_is_single_canary_only": claims["v4_2_gate_passed"] is True
        and claims["v4_2_single_canary_allowed"] is True
        and claims["v4_2_paid_expansion_allowed"] is False,
        "v4_2_transform_evidence_container_cleared": claims["v4_2_candidate_variant"]
        == "adaptive_harness_workspace_affordance_v4_2"
        and claims["v4_2_paid_canary_allowed"] is True
        and claims["v4_2_source_hash_matches"] is True
        and claims["v4_2_profile_fingerprint_matches"] is True
        and claims["v4_2_transform_manifest_evidence_wired"] is True
        and claims["v4_2_production_transform_manifest_bridge_wired"] is True
        and claims["v4_2_new_full_repo_lint_findings"] == 0
        and claims["v4_2_readiness"] is True,
        "deerflow_base_limitation_is_fresh_controlled_not_universal": (
            claims["deerflow_attribution_gate_passed"] is True
            and all((claims["deerflow_attribution_control_fields"] or {}).values())
            and claims["deerflow_attribution_allowed_runs"] == 1
            and claims["deerflow_attribution_readiness"] is True
            and (claims["deerflow_historical_vanilla"] or {}).get("task_count") == 16
            and (claims["deerflow_historical_vanilla"] or {}).get("passed") == 6
            and (claims["deerflow_historical_vanilla"] or {}).get(
                "comparable_to_current"
            )
            is False
            and claims["deerflow_attribution_both_failed"] is True
            and claims["deerflow_attribution_pair_valid"] is False
            and (claims["deerflow_attribution_model_visible_surface"] or {}).get("equal")
            is False
            and (claims["deerflow_attribution_vanilla"] or {}).get("total_tokens")
            == 321950
            and (claims["deerflow_attribution_adaptive"] or {}).get("total_tokens")
            == 220496
            and claims["deerflow_attribution_vanilla_no_final_response"] is True
            and claims["deerflow_attribution_vanilla_sandbox_false_positive_count"] == 2
            and (claims["deerflow_attribution_result"] or {}).get(
                "deerflow_base_ceiling_supported"
            )
            is True
            and (claims["deerflow_attribution_result"] or {}).get(
                "harness_quality_uplift_supported"
            )
            is False
            and claims["deerflow_attribution_decision"]
            == "keep_deerflow_as_one_runtime_backend_not_the_project_base"
            and claims["deerflow_attribution_paid_expansion_allowed"] is False
        ),
        "v4_2_full_minibench_failure_is_frozen_and_rejected": (
            claims["v4_2_minibench_task_count"] == 16
            and claims["v4_2_minibench_passed"] == 0
            and claims["v4_2_minibench_total_tokens"] == 5152350
            and claims["v4_2_minibench_tool_calls"] == 510
            and claims["v4_2_minibench_output_file_count"] == 6
            and claims["v4_2_minibench_integrity_passed_tasks"] == 16
            and [item.get("task_count") for item in claims["v4_2_minibench_partitions"] or ()]
            == [8, 4, 4]
            and [item.get("passed") for item in claims["v4_2_minibench_partitions"] or ()]
            == [0, 0, 0]
            and len(claims["v4_2_minibench_partial_quality_tasks"] or ()) == 5
            and (claims["v4_2_minibench_failure_synthesis"] or {}).get(
                "all_integrity_passed"
            )
            is True
            and (claims["v4_2_minibench_failure_synthesis"] or {}).get("scope")
            == "v4.2 full MiniBench16 public failure synthesis"
            and "full isolated MiniBench16 8/4/4 run"
            in str(
                (claims["v4_2_minibench_failure_synthesis"] or {}).get(
                    "claim_boundary"
                )
                or ""
            )
            and len(
                (
                    (claims["v4_2_minibench_failure_synthesis"] or {}).get(
                        "cross_type_signals"
                    )
                    or {}
                ).get("missing_required_artifact_runs")
                or ()
            )
            == 10
            and claims["v4_2_minibench_decision"]
            == "reject_v4_2_and_stop_all_paid_expansion"
            and claims["v4_2_minibench_paid_expansion_allowed"] is False
        ),
        "runtime_selection_fails_closed_without_qualified_backend": (
            len(claims["runtime_capability_profiles"] or ()) == 2
            and len(claims["runtime_capability_profile_fingerprints"] or {}) == 2
            and all((claims["runtime_capability_invariants"] or {}).values())
            and (
                (claims["runtime_capability_decisions"] or {}).get("strict_file")
                or {}
            ).get("selected_runtime_id")
            is None
            and (
                (claims["runtime_capability_decisions"] or {}).get("strict_browser")
                or {}
            ).get("selected_runtime_id")
            is None
            and (
                (claims["runtime_capability_decisions"] or {}).get("control_plane")
                or {}
            ).get("selected_runtime_id")
            == "deterministic-reference"
            and claims["runtime_capability_execution_performed"] is False
            and claims["runtime_capability_paid_expansion_allowed"] is False
        ),
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
            "to one replacement Development canary only. A29 records that v3.1 cut observed Tokens and "
            "latency but still produced no artifact. A30/A31 bind the loopback public-source materializer "
            "and early cache-block turn termination to one v3.2 canary. A32 records that source access "
            "and artifact creation recovered, but an all-empty JSON plus a runtime-limit terminal only "
            "reached 1/5 and was not promoted. A33/A34 bind v3.3 public non-vacuity review, runtime-error "
            "completion rejection, direct synthesis transforms, and fail-closed textual tool errors to "
            "one replacement Development canary. A35 records that v3.3 correctly rejected the empty "
            "completion but lost Evidence assessments behind the Response gate and produced no artifact. "
            "A36/A37 preserve those assessments and bind DeepSeek thinking=enabled with reasoning_effort=max "
            "through the actual pinned-container model factory for v3.4. A38 records that max thinking "
            "still reached only 1/5 while consuming 719,574 Tokens and ending on a mutation-epoch "
            "regression, so v2.6 remains selected. A39 binds the fix to a durable PolicySession run "
            "identity and proves object-shaped DeerFlow contexts keep epoch [1,1]. A40/A41 then freeze "
            "v3.5 at thinking=enabled, reasoning_effort=high for one same-task canary only. A42 records "
            "that the lifecycle error disappeared and quality returned to 2/5, but the artifact copied a "
            "known public draft result, Token use reached 765,768, and v2.6 remained the selected Shadow. "
            "A43/A44 add a hash-only claim/source lineage core and prove that the A42 public intermediate "
            "copy is rejected in replay and in the pinned container. A45 records a v3.6 recovery-order "
            "regression: blocked grounding masked the missing artifact, so no output was produced. A46/A47 "
            "verify that only an explicitly unsatisfied grounding criterion becomes a lineage failure and "
            "artifact delivery recovery remains available. A48 records that v3.7 requested delivery but "
            "the model spent the turn writing non-output helper scripts and still produced no artifact. "
            "A49/A50 make delivery recovery reject non-output writes while preserving task transforms and "
            "environment interactions. A51 records that v3.8 then wrote an empty placeholder, treated the "
            "failed public shape as state inconsistency, and exhausted 858,909 Tokens without passing. "
            "A52/A53 separate missing Artifact delivery from invalid Artifact repair, bound each to one "
            "attempt, and re-arm delivery by user directive generation in the pinned container. No paid "
            "v3.9 run has been performed. "
            "A54 freezes a MiniBench16-disjoint Diagnostic4 with one low-cost Development task per "
            "file/CLI/browser/API type and high thinking as the breadth-first feedback stage. "
            "A55 records all four first samples: 0/4 passed, 1,206,127 total Tokens, 129 Tool calls, "
            "and cross-type required-artifact, directory-observation, environment-observation, and "
            "no-progress failures. A56/A57 bind v4 required-target delivery, directory artifacts, "
            "OSError containment, and a two-violation early turn stop with zero model calls. "
            "A58 records that the first v4 File replay still scored 0/5 at 337,994 Tokens: it "
            "targeted the correct artifact but DeerFlow rejected the write because the model omitted "
            "the non-semantic description field, and one model batch produced 15 rejected delivery "
            "results. A59/A60 bind v4.1 compatibility argument repair and delivery-batch early stop. "
            "A61 records the v4.1 File replay: quality remained 0/5 and no artifact was delivered, "
            "but observed Tokens fell 34.8%, Tool calls fell from 45 to 31, and latency fell 65.1%; "
            "the next public failure is the task transform resolving `/task/snapshots` plus blocked "
            "unsafe path workarounds. v4.1 remains Shadow and paid expansion stays disabled. "
            "A62 adds a zero-execution Python Transform Manifest and detects the same missing "
            "`snapshots/manifest.json` dependency from public AST/path semantics before model use. "
            "A63/A64 integrate only a relevant, 4,000-character-bounded Manifest summary into the "
            "before-run Evidence layer and prove pinned-container wiring with zero model calls; "
            "Transform execution and Capsule remain disabled. "
            "A65 freezes a fresh current-model/high-thinking Vanilla control because the historical "
            "Vanilla 6/16 used a different model, disabled thinking, and Mimo vision. A66 shows both "
            "fresh Vanilla and Adaptive scored 0/5 with no artifact; Vanilla also returned no final "
            "response and hit two Sandbox path false positives. The Harness used fewer Tokens and "
            "latency but more Tool calls, so DeerFlow is retained as one backend rather than the "
            "project base, without claiming a universal runtime ranking. "
            "A67 records the user-requested full isolated v4.2 MiniBench16 run: Development 0/8, "
            "Transfer 0/4, Held-out 0/4, 5,152,350 Tokens, 510 Tool calls, six output files, and "
            "all 16 integrity checks passed. Five tasks had partial public capacity but none passed; "
            "v4.2 is rejected and all further paid expansion is stopped. "
            "A68 adds Runtime Capability attestations and deterministic selection: neither current "
            "backend qualifies for a strict real File or Browser request, DeerFlow is selectable "
            "only when claimed final-response assurance is explicitly accepted, and the deterministic "
            "reference is selected only for control-plane Conformance. No Runtime ranking is claimed. "
            "This is a copy guard, not full factual verification. Transfer, "
            "Held-out, and further task expansion remain disabled."
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


def _adaptive_source_sha256() -> str:
    source_root = ROOT / "src/adaptive_harness"
    digest = hashlib.sha256()
    for path in sorted(
        candidate
        for candidate in source_root.rglob("*.py")
        if candidate.is_file() and "__pycache__" not in candidate.parts
    ):
        digest.update(path.relative_to(source_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
