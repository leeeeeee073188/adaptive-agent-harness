from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_live_evolution import analyze_live_runs


def _run(root: Path, name: str, variant: str, capacity: float, tokens: int) -> Path:
    run = root / name
    task = run / "task"
    (task / "agent").mkdir(parents=True)
    (run / "summary.json").write_text(
        json.dumps(
            {
                "run_id": name,
                "model_name": "model",
                "experiment": {"variant": variant},
                "results": [
                    {
                        "task_id": "task-1",
                        "run_dir": str(task),
                        "passed": capacity == 1.0,
                        "capacity_score": capacity,
                        "checks_passed": int(capacity * 5),
                        "checks_total": 5,
                        "usage": {"total_tokens": tokens},
                        "tool_call_count": 10,
                        "elapsed_sec": 10.0,
                        "output_file_count": int(capacity > 0),
                        "integrity_passed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (task / "agent/adaptive-ledger.jsonl").write_text("", encoding="utf-8")
    return run


class LiveEvolutionAnalysisTests(unittest.TestCase):
    def test_selects_highest_capacity_then_lower_cost_and_keeps_failure_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = _run(root, "baseline", "vanilla_deerflow", 0.0, 100)
            expensive = _run(root, "expensive", "candidate-a", 0.4, 120)
            selected = _run(root, "selected", "candidate-b", 0.4, 80)

            report = analyze_live_runs([baseline, expensive, selected], "task-1")

        self.assertEqual(report["selected_candidate_run_id"], "selected")
        self.assertEqual(report["selected_candidate_decision"], "keep_shadow")
        self.assertFalse(report["paid_expansion_allowed"])
        selected_row = next(row for row in report["runs"] if row["run_id"] == "selected")
        self.assertAlmostEqual(selected_row["delta_vs_baseline"]["token_fraction"], -0.2)
        self.assertNotIn("public_check_results", selected_row)

    def test_rejects_missing_or_duplicate_baseline_and_missing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = _run(root, "candidate", "candidate-a", 0.4, 80)
            with self.assertRaisesRegex(ValueError, "exactly one vanilla_deerflow baseline"):
                analyze_live_runs([candidate], "task-1")

            baseline_a = _run(root, "baseline-a", "vanilla_deerflow", 0.0, 100)
            baseline_b = _run(root, "baseline-b", "vanilla_deerflow", 0.0, 110)
            with self.assertRaisesRegex(ValueError, "found 2"):
                analyze_live_runs([baseline_a, baseline_b, candidate], "task-1")

            with self.assertRaisesRegex(ValueError, "non-baseline candidate"):
                analyze_live_runs([baseline_a], "task-1")

    def test_single_task_pass_stays_shadow_and_cannot_authorize_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = _run(root, "baseline", "vanilla_deerflow", 0.0, 100)
            candidate = _run(root, "candidate", "candidate-a", 1.0, 80)

            report = analyze_live_runs([baseline, candidate], "task-1")

        self.assertTrue(report["selected_candidate_passed"])
        self.assertEqual(report["selected_candidate_decision"], "keep_shadow")
        self.assertFalse(report["paid_expansion_allowed"])
        self.assertIn("passed this task", report["claim_boundary"])


if __name__ == "__main__":
    unittest.main()
