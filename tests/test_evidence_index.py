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
