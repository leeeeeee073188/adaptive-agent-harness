from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.build_evidence_index import build_index


class EvidenceIndexTests(unittest.TestCase):
    def test_checked_in_evidence_supports_resume_safe_claims(self) -> None:
        root = Path(__file__).resolve().parents[1]

        index = build_index(root / "evidence")

        self.assertTrue(index["verified"])
        self.assertEqual(index["claims"]["zero_model_unit_tests"], 179)
        self.assertEqual(index["claims"]["tool_advice_stage_unit_tests"], 93)
        self.assertEqual(index["claims"]["current_realreplica_unit_tests"], 67)
        self.assertEqual(index["claims"]["minibench_task_count"], 16)
        self.assertEqual(index["claims"]["provider_enforced_task_coverage"], 16)
        self.assertEqual(index["claims"]["completion_failures_blocked"], 5)
        self.assertEqual(index["claims"]["completion_successes_falsely_blocked"], 0)
        self.assertFalse(index["claims"]["recovery_practice_enabled"])
        self.assertFalse(index["claims"]["browser_guard_deployed"])
        self.assertEqual(index["claims"]["context_historical_snapshot_count"], 99)
        self.assertFalse(index["claims"]["context_v1_1_promoted"])
        self.assertEqual(index["claims"]["context_v1_2_status"], "shadow_observed")
        self.assertEqual(index["claims"]["context_v1_2_decision"], "keep_shadow")
        self.assertEqual(index["claims"]["action_ledger_records"], 200)
        self.assertFalse(index["claims"]["action_ledger_enforcement_ready"])
        self.assertEqual(index["claims"]["action_ledger_v1_3_status"], "shadow_unexecuted")
        self.assertTrue(index["claims"]["tool_advice_gate_eligible"])
        self.assertFalse(index["claims"]["tool_enforcement_gate_eligible"])
        self.assertEqual(index["claims"]["tool_advice_v1_5_status"], "shadow_observed")
        self.assertEqual(index["claims"]["tool_advice_v1_5_advice_applied"], 0)
        self.assertTrue(index["claims"]["runtime_conformance_passed"])
        self.assertTrue(index["claims"]["runtime_evolution_zero_model_passed"])
        self.assertEqual(index["claims"]["runtime_evolution_model_calls"], 0)
        self.assertEqual(
            index["claims"]["runtime_evolution_role_counts"],
            {"development": 8, "heldout": 4, "transfer": 4},
        )
        self.assertTrue(index["claims"]["runtime_evolution_wiring_passed"])
        self.assertFalse(index["claims"]["runtime_evolution_paid_candidate_allowed"])
        self.assertTrue(index["claims"]["runtime_evolution_single_canary_default"])
        self.assertTrue(index["claims"]["runtime_evolution_review_passed"])
        self.assertEqual(
            index["claims"]["live_selected_candidate_variant"],
            "adaptive_harness_runtime_evolution_v2_6",
        )
        self.assertEqual(index["claims"]["live_selected_candidate_decision"], "keep_shadow")
        self.assertFalse(index["claims"]["live_paid_expansion_allowed"])
        self.assertFalse(index["claims"]["live_selected_passed"])
        self.assertEqual(index["claims"]["live_selected_capacity_score"], 0.4)
        self.assertLess(index["claims"]["live_selected_token_fraction_vs_baseline"], 0)
        self.assertEqual(index["claims"]["failure_analysis_model_calls"], 0)
        self.assertEqual(index["claims"]["failure_analysis_run_count"], 6)
        self.assertTrue(
            index["claims"]["failure_analysis_v2_6_signals"][
                "durable_failure_state_missing_from_model_context"
            ]
        )
        self.assertTrue(
            index["claims"]["failure_analysis_v2_7_signals"][
                "mutation_epoch_regression"
            ]
        )
        self.assertTrue(index["claims"]["next_generation_gate_passed"])
        self.assertTrue(index["claims"]["next_generation_single_canary_allowed"])
        self.assertFalse(index["claims"]["next_generation_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["next_generation_visible_workspace_selected"])
        self.assertEqual(index["claims"]["next_generation_source_criterion_count"], 2)
        self.assertTrue(index["claims"]["next_generation_historical_artifact_rejected"])
        self.assertEqual(
            index["claims"]["v3_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3",
        )
        self.assertTrue(index["claims"]["v3_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_profile_fingerprint_matches"])
        self.assertEqual(
            index["claims"]["v3_live_selected_candidate"],
            "adaptive_harness_runtime_evolution_v2_6",
        )
        self.assertFalse(index["claims"]["v3_live_row"]["passed"])
        self.assertEqual(index["claims"]["v3_live_row"]["capacity_score"], 0.0)
        self.assertFalse(index["claims"]["v3_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_1_gate_passed"])
        self.assertTrue(index["claims"]["v3_1_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_1_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_1_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3_1",
        )
        self.assertTrue(index["claims"]["v3_1_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_1_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_1_profile_fingerprint_matches"])
        self.assertEqual(
            index["claims"]["v3_1_live_selected_candidate"],
            "adaptive_harness_runtime_evolution_v2_6",
        )
        self.assertEqual(index["claims"]["v3_1_live_row"]["capacity_score"], 0.0)
        self.assertEqual(index["claims"]["v3_1_live_row"]["total_tokens"], 216688)
        self.assertFalse(index["claims"]["v3_1_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_2_gate_passed"])
        self.assertTrue(index["claims"]["v3_2_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_2_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_2_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3_2",
        )
        self.assertTrue(index["claims"]["v3_2_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_2_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_2_profile_fingerprint_matches"])
        self.assertEqual(index["claims"]["v3_2_live_row"]["capacity_score"], 0.2)
        self.assertEqual(index["claims"]["v3_2_live_row"]["output_file_count"], 1)
        self.assertFalse(index["claims"]["v3_2_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_3_gate_passed"])
        self.assertTrue(index["claims"]["v3_3_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_3_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_3_non_vacuity_present"])
        self.assertEqual(
            index["claims"]["v3_3_historical_artifact_diagnostics"],
            ["all_collections_empty"],
        )
        self.assertTrue(index["claims"]["v3_3_runtime_limit_response_rejected"])
        self.assertTrue(index["claims"]["v3_3_direct_script_transform"])
        self.assertEqual(
            index["claims"]["v3_3_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3_3",
        )
        self.assertTrue(index["claims"]["v3_3_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_3_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_3_profile_fingerprint_matches"])
        self.assertEqual(index["claims"]["v3_3_live_row"]["capacity_score"], 0.0)
        self.assertEqual(index["claims"]["v3_3_live_row"]["output_file_count"], 0)
        self.assertEqual(index["claims"]["v3_3_live_row"]["total_tokens"], 277043)
        self.assertFalse(index["claims"]["v3_3_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_4_gate_passed"])
        self.assertTrue(index["claims"]["v3_4_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_4_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_4_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3_4",
        )
        self.assertTrue(index["claims"]["v3_4_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_4_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_4_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v3_4_thinking_max_configured"])
        self.assertTrue(index["claims"]["v3_4_completion_conjunction_verified"])
        self.assertEqual(index["claims"]["v3_4_live_row"]["capacity_score"], 0.2)
        self.assertEqual(index["claims"]["v3_4_live_row"]["total_tokens"], 719574)
        self.assertEqual(index["claims"]["v3_4_live_row"]["tool_calls"], 64)
        self.assertFalse(index["claims"]["v3_4_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["mutation_epoch_offline_gate_passed"])
        self.assertTrue(index["claims"]["mutation_epoch_object_context_monotonic"])
        self.assertTrue(index["claims"]["v3_5_gate_passed"])
        self.assertTrue(index["claims"]["v3_5_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_5_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_5_candidate_variant"],
            "adaptive_harness_evidence_workspace_v3_5",
        )
        self.assertTrue(index["claims"]["v3_5_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_5_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_5_profile_fingerprint_matches"])
        self.assertEqual(index["claims"]["v3_5_thinking_effort"], "high")
        self.assertTrue(index["claims"]["v3_5_thinking_request_configured"])
        self.assertTrue(index["claims"]["v3_5_epoch_monotonic"])
        self.assertEqual(index["claims"]["v3_5_live_row"]["capacity_score"], 0.4)
        self.assertEqual(index["claims"]["v3_5_live_row"]["total_tokens"], 765768)
        self.assertEqual(index["claims"]["v3_5_live_row"]["tool_calls"], 62)
        self.assertFalse(
            index["claims"]["v3_5_diagnosis_row"]["signals"][
                "mutation_epoch_regression"
            ]
        )
        self.assertFalse(index["claims"]["v3_5_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["source_grounding_gate_passed"])
        self.assertTrue(index["claims"]["source_grounding_single_canary_allowed"])
        self.assertFalse(index["claims"]["source_grounding_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["source_grounding_contract_present"])
        self.assertTrue(index["claims"]["source_grounding_historical_copy_rejected"])
        self.assertEqual(
            index["claims"]["source_grounding_candidate_variant"],
            "adaptive_harness_source_grounding_v3_6",
        )
        self.assertTrue(index["claims"]["source_grounding_paid_canary_allowed"])
        self.assertTrue(index["claims"]["source_grounding_source_hash_matches"])
        self.assertTrue(index["claims"]["source_grounding_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["source_grounding_container_copy_guard"])
        self.assertEqual(index["claims"]["v3_6_live_row"]["capacity_score"], 0.0)
        self.assertEqual(index["claims"]["v3_6_live_row"]["total_tokens"], 320755)
        self.assertEqual(index["claims"]["v3_6_live_row"]["output_file_count"], 0)
        self.assertFalse(index["claims"]["v3_6_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_7_gate_passed"])
        self.assertTrue(index["claims"]["v3_7_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_7_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_7_candidate_variant"],
            "adaptive_harness_source_grounding_v3_7",
        )
        self.assertTrue(index["claims"]["v3_7_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_7_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_7_profile_fingerprint_matches"])
        self.assertTrue(
            index["claims"]["v3_7_blocked_grounding_preserves_artifact_recovery"]
        )
        self.assertEqual(index["claims"]["v3_7_live_row"]["capacity_score"], 0.0)
        self.assertEqual(index["claims"]["v3_7_live_row"]["total_tokens"], 439869)
        self.assertEqual(index["claims"]["v3_7_live_row"]["output_file_count"], 0)
        self.assertFalse(index["claims"]["v3_7_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v3_8_gate_passed"])
        self.assertTrue(index["claims"]["v3_8_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_8_paid_expansion_allowed"])
        self.assertEqual(index["claims"]["v3_8_candidate_variant"], "adaptive_harness_source_grounding_v3_8")
        self.assertTrue(index["claims"]["v3_8_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_8_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_8_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v3_8_delivery_non_output_write_blocked"])
        self.assertEqual(index["claims"]["v3_8_live_row"]["capacity_score"], 0.2)
        self.assertEqual(index["claims"]["v3_8_live_row"]["total_tokens"], 858909)
        self.assertEqual(index["claims"]["v3_8_live_row"]["output_file_count"], 1)
        self.assertFalse(index["claims"]["v3_8_live_paid_expansion_allowed"])
        self.assertFalse(
            index["claims"]["v3_8_diagnosis_row"]["signals"][
                "mutation_epoch_regression"
            ]
        )
        self.assertTrue(index["claims"]["v3_9_gate_passed"])
        self.assertTrue(index["claims"]["v3_9_single_canary_allowed"])
        self.assertFalse(index["claims"]["v3_9_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v3_9_candidate_variant"],
            "adaptive_harness_source_grounding_v3_9",
        )
        self.assertTrue(index["claims"]["v3_9_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v3_9_source_hash_matches"])
        self.assertTrue(index["claims"]["v3_9_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v3_9_delivery_rearmed_after_invalid_artifact"])
        self.assertTrue(index["claims"]["v3_9_invalid_artifact_recovery_rewrites_output"])
        self.assertTrue(index["claims"]["diagnostic4_passed"])
        self.assertEqual(len(index["claims"]["diagnostic4_task_ids"]), 4)
        self.assertEqual(
            index["claims"]["diagnostic4_counts"]["category"],
            {"api": 1, "browser": 1, "cli": 1, "file": 1},
        )
        self.assertEqual(
            index["claims"]["diagnostic4_counts"]["difficulty_band"],
            {"easy": 2, "medium": 2},
        )
        self.assertTrue(index["claims"]["diagnostic4_thinking_high_default"])
        self.assertTrue(index["claims"]["diagnostic4_minibench16_disjoint"])
        self.assertEqual(
            len(index["claims"]["diagnostic4_optimization_dimensions"]),
            10,
        )
        self.assertEqual(index["claims"]["diagnostic4_new_lint_findings"], 0)
        self.assertTrue(index["claims"]["diagnostic4_readiness"])
        self.assertFalse(index["claims"]["diagnostic4_paid_expansion_allowed"])
        self.assertEqual(index["claims"]["diagnostic4_live_run_count"], 4)
        self.assertEqual(
            index["claims"]["diagnostic4_live_category_counts"],
            {"api": 1, "browser": 1, "cli": 1, "file": 1},
        )
        self.assertTrue(index["claims"]["diagnostic4_live_all_integrity_passed"])
        self.assertEqual(index["claims"]["diagnostic4_live_passed_runs"], 0)
        self.assertEqual(
            index["claims"]["diagnostic4_live_cost"]["total_tokens"],
            1206127,
        )
        self.assertFalse(index["claims"]["diagnostic4_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v4_gate_passed"])
        self.assertTrue(index["claims"]["v4_single_canary_allowed"])
        self.assertFalse(index["claims"]["v4_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v4_candidate_variant"],
            "adaptive_harness_cross_type_artifact_v4_0",
        )
        self.assertTrue(index["claims"]["v4_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v4_source_hash_matches"])
        self.assertTrue(index["claims"]["v4_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v4_required_artifact_target_enforced"])
        self.assertTrue(index["claims"]["v4_required_directory_artifact_observed"])
        self.assertTrue(index["claims"]["v4_turn_observation_oserror_fails_closed"])
        self.assertTrue(index["claims"]["v4_delivery_violation_budget_enforced"])
        self.assertEqual(index["claims"]["v4_new_full_repo_lint_findings"], 0)
        self.assertTrue(index["claims"]["v4_readiness"])
        self.assertFalse(index["claims"]["v4_live_candidate"]["passed"])
        self.assertEqual(index["claims"]["v4_live_candidate"]["total_tokens"], 337994)
        self.assertTrue(index["claims"]["v4_live_correct_required_target_attempted"])
        self.assertTrue(index["claims"]["v4_live_correct_write_failed_missing_description"])
        self.assertEqual(index["claims"]["v4_live_repeated_delivery_early_end_results"], 15)
        self.assertFalse(index["claims"]["v4_live_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v4_1_gate_passed"])
        self.assertTrue(index["claims"]["v4_1_single_canary_allowed"])
        self.assertFalse(index["claims"]["v4_1_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v4_1_candidate_variant"],
            "adaptive_harness_cross_type_artifact_v4_1",
        )
        self.assertTrue(index["claims"]["v4_1_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v4_1_source_hash_matches"])
        self.assertEqual(len(index["claims"]["current_adaptive_source_sha256"]), 64)
        self.assertTrue(index["claims"]["v4_1_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v4_1_delivery_batch_guard_enforced"])
        self.assertTrue(index["claims"]["v4_1_write_description_repair_enforced"])
        self.assertEqual(index["claims"]["v4_1_new_full_repo_lint_findings"], 0)
        self.assertTrue(index["claims"]["v4_1_readiness"])
        self.assertFalse(index["claims"]["v4_1_live_candidate"]["passed"])
        self.assertEqual(index["claims"]["v4_1_live_candidate"]["total_tokens"], 220496)
        self.assertEqual(index["claims"]["v4_1_live_candidate"]["tool_calls"], 31)
        self.assertEqual(index["claims"]["v4_1_live_cost"]["token_delta"], -117498)
        self.assertEqual(index["claims"]["v4_1_live_cost"]["tool_call_delta"], -14)
        self.assertEqual(index["claims"]["v4_1_live_delivery_batch_guard_result_count"], 2)
        self.assertEqual(index["claims"]["v4_1_live_compatibility_repairs_applied"], 0)
        self.assertTrue(index["claims"]["v4_1_live_task_transform_missing_snapshot_error"])
        self.assertTrue(index["claims"]["v4_1_live_unsafe_path_attempt_blocked"])
        self.assertFalse(index["claims"]["v4_1_live_paid_expansion_allowed"])
        self.assertEqual(index["claims"]["transform_manifest_model_calls"], 0)
        self.assertFalse(index["claims"]["transform_manifest_execution_performed"])
        self.assertEqual(index["claims"]["transform_manifest_missing_read_count"], 1)
        self.assertEqual(
            index["claims"]["transform_manifest_row"]["script_path"],
            "workspace/analysis/audit.py",
        )
        self.assertFalse(index["claims"]["transform_manifest_paid_expansion_allowed"])
        self.assertTrue(index["claims"]["v4_2_gate_passed"])
        self.assertTrue(index["claims"]["v4_2_single_canary_allowed"])
        self.assertFalse(index["claims"]["v4_2_paid_expansion_allowed"])
        self.assertEqual(
            index["claims"]["v4_2_candidate_variant"],
            "adaptive_harness_workspace_affordance_v4_2",
        )
        self.assertTrue(index["claims"]["v4_2_paid_canary_allowed"])
        self.assertTrue(index["claims"]["v4_2_source_hash_matches"])
        self.assertEqual(len(index["claims"]["current_adaptive_source_sha256"]), 64)
        self.assertTrue(index["claims"]["v4_2_profile_fingerprint_matches"])
        self.assertTrue(index["claims"]["v4_2_transform_manifest_evidence_wired"])
        self.assertTrue(
            index["claims"]["v4_2_production_transform_manifest_bridge_wired"]
        )
        self.assertEqual(index["claims"]["v4_2_new_full_repo_lint_findings"], 0)
        self.assertTrue(index["claims"]["v4_2_readiness"])
        self.assertTrue(index["claims"]["deerflow_attribution_gate_passed"])
        self.assertTrue(all(index["claims"]["deerflow_attribution_control_fields"].values()))
        self.assertEqual(index["claims"]["deerflow_attribution_allowed_runs"], 1)
        self.assertEqual(index["claims"]["deerflow_historical_vanilla"]["task_count"], 16)
        self.assertEqual(index["claims"]["deerflow_historical_vanilla"]["passed"], 6)
        self.assertFalse(
            index["claims"]["deerflow_historical_vanilla"]["comparable_to_current"]
        )
        self.assertTrue(index["claims"]["deerflow_attribution_readiness"])
        self.assertTrue(index["claims"]["deerflow_attribution_both_failed"])
        self.assertFalse(index["claims"]["deerflow_attribution_pair_valid"])
        self.assertFalse(
            index["claims"]["deerflow_attribution_model_visible_surface"]["equal"]
        )
        self.assertEqual(index["claims"]["deerflow_attribution_vanilla"]["total_tokens"], 321950)
        self.assertEqual(index["claims"]["deerflow_attribution_adaptive"]["total_tokens"], 220496)
        self.assertTrue(index["claims"]["deerflow_attribution_vanilla_no_final_response"])
        self.assertEqual(
            index["claims"]["deerflow_attribution_vanilla_sandbox_false_positive_count"],
            2,
        )
        self.assertTrue(
            index["claims"]["deerflow_attribution_result"][
                "deerflow_base_ceiling_supported"
            ]
        )
        self.assertFalse(
            index["claims"]["deerflow_attribution_result"][
                "harness_quality_uplift_supported"
            ]
        )
        self.assertFalse(index["claims"]["deerflow_attribution_paid_expansion_allowed"])
        self.assertEqual(index["claims"]["v4_2_minibench_task_count"], 16)
        self.assertEqual(index["claims"]["v4_2_minibench_passed"], 0)
        self.assertEqual(index["claims"]["v4_2_minibench_total_tokens"], 5152350)
        self.assertEqual(index["claims"]["v4_2_minibench_tool_calls"], 510)
        self.assertEqual(index["claims"]["v4_2_minibench_output_file_count"], 6)
        self.assertEqual(index["claims"]["v4_2_minibench_integrity_passed_tasks"], 16)
        self.assertEqual(
            [item["task_count"] for item in index["claims"]["v4_2_minibench_partitions"]],
            [8, 4, 4],
        )
        self.assertEqual(len(index["claims"]["v4_2_minibench_partial_quality_tasks"]), 5)
        self.assertTrue(
            index["claims"]["v4_2_minibench_failure_synthesis"][
                "all_integrity_passed"
            ]
        )
        self.assertEqual(
            index["claims"]["v4_2_minibench_failure_synthesis"]["scope"],
            "v4.2 full MiniBench16 public failure synthesis",
        )
        self.assertIn(
            "full isolated MiniBench16 8/4/4 run",
            index["claims"]["v4_2_minibench_failure_synthesis"][
                "claim_boundary"
            ],
        )
        self.assertFalse(index["claims"]["v4_2_minibench_paid_expansion_allowed"])
        self.assertEqual(len(index["claims"]["runtime_capability_profiles"]), 2)
        self.assertEqual(
            len(index["claims"]["runtime_capability_profile_fingerprints"]),
            2,
        )
        self.assertTrue(all(index["claims"]["runtime_capability_invariants"].values()))
        self.assertIsNone(
            index["claims"]["runtime_capability_decisions"]["strict_file"][
                "selected_runtime_id"
            ]
        )
        self.assertEqual(
            index["claims"]["runtime_capability_decisions"]["control_plane"][
                "selected_runtime_id"
            ],
            "deterministic-reference",
        )
        self.assertFalse(index["claims"]["runtime_capability_execution_performed"])
        self.assertFalse(index["claims"]["runtime_capability_paid_expansion_allowed"])
        self.assertEqual(index["secret_findings"], [])

    def test_rejects_live_evidence_without_a_token_improvement(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "evidence"
            shutil.copytree(root / "evidence", copied)
            summary_path = copied / "a22-live-model-evolution/summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            selected = next(row for row in summary["runs"] if row["run_id"] == summary["selected_candidate_run_id"])
            selected["delta_vs_baseline"]["token_fraction"] = 0.01
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            index = build_index(copied)

        self.assertFalse(index["verified"])
        self.assertFalse(index["invariants"]["live_candidate_has_partial_measured_gain"])

    def test_rejects_matching_current_candidate_hashes_when_both_are_stale(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "evidence"
            shutil.copytree(root / "evidence", copied)
            stale = "0" * 64
            for relative in (
                "a63-v4-2-transform-evidence-gate/summary.json",
                "a64-v4-2-transform-evidence-container/container-wiring.json",
            ):
                path = copied / relative
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["adaptive_source_sha256"] = stale
                path.write_text(json.dumps(payload), encoding="utf-8")

            index = build_index(copied)

        self.assertFalse(index["claims"]["v4_2_source_hash_matches"])
        self.assertFalse(index["invariants"]["v4_2_transform_evidence_container_cleared"])
        self.assertFalse(index["verified"])


if __name__ == "__main__":
    unittest.main()
