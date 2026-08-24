from __future__ import annotations

import unittest
from pathlib import Path

from scripts.build_evidence_index import build_index


class EvidenceIndexTests(unittest.TestCase):
    def test_checked_in_evidence_supports_resume_safe_claims(self) -> None:
        root = Path(__file__).resolve().parents[1]

        index = build_index(root / "evidence")

        self.assertTrue(index["verified"])
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
        self.assertEqual(index["secret_findings"], [])


if __name__ == "__main__":
    unittest.main()
