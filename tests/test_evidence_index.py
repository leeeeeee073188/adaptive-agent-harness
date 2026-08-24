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


if __name__ == "__main__":
    unittest.main()
