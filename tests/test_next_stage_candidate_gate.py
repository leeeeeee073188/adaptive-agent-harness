from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_next_stage import _candidate_gate_valid, _gate_exit_code


class NextStageCandidateGateTests(unittest.TestCase):
    def test_gate_requires_zero_model_boundary_and_matching_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = root / "gate.json"
            wiring = root / "wiring.json"
            gate.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "candidate_single_development_canary_allowed": True,
                        "paid_expansion_allowed": False,
                        "model_calls": 0,
                        "new_model_tokens": 0,
                        "adaptive_source_sha256": "source",
                        "executable_policy_profile_fingerprint": "profile",
                    }
                ),
                encoding="utf-8",
            )
            wiring.write_text(
                json.dumps(
                    {
                        "adaptive_source_sha256": "source",
                        "executable_policy_profile_fingerprint": "profile",
                    }
                ),
                encoding="utf-8",
            )

            valid = _candidate_gate_valid(gate, wiring_evidence=wiring)
            document = json.loads(gate.read_text(encoding="utf-8"))
            document["paid_expansion_allowed"] = True
            gate.write_text(json.dumps(document), encoding="utf-8")
            expanded = _candidate_gate_valid(gate, wiring_evidence=wiring)
            document["paid_expansion_allowed"] = False
            document["adaptive_source_sha256"] = "other"
            gate.write_text(json.dumps(document), encoding="utf-8")
            mismatched = _candidate_gate_valid(gate, wiring_evidence=wiring)

        self.assertTrue(valid)
        self.assertFalse(expanded)
        self.assertFalse(mismatched)

    def test_missing_or_invalid_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid = root / "invalid.json"
            invalid.write_text("not-json", encoding="utf-8")

            self.assertFalse(_candidate_gate_valid(None, wiring_evidence=invalid))
            self.assertFalse(_candidate_gate_valid(invalid, wiring_evidence=invalid))

    def test_paid_canary_mode_exit_code_tracks_paid_decision(self) -> None:
        blocked = {
            "zero_model_passed": True,
            "paid_candidate_canary": {"allowed": False},
        }
        allowed = {
            "zero_model_passed": True,
            "paid_candidate_canary": {"allowed": True},
        }

        self.assertEqual(_gate_exit_code(blocked, "zero-model"), 0)
        self.assertEqual(_gate_exit_code(blocked, "paid-canary"), 1)
        self.assertEqual(_gate_exit_code(allowed, "paid-canary"), 0)
        with self.assertRaisesRegex(ValueError, "unknown gate mode"):
            _gate_exit_code(allowed, "other")


if __name__ == "__main__":
    unittest.main()
