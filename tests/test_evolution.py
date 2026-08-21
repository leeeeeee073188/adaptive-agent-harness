from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.config import Bundle, PluginSpec, Profile
from adaptive_harness.evolution import (
    EvolutionCandidate,
    EvolutionDisposition,
    EvolutionManager,
    ShadowEvaluation,
    VersionStatus,
)
from adaptive_harness.ledger import SessionLedger


def _profile(name: str, retry_budget: int) -> Profile:
    return Profile(
        name,
        (Bundle("runtime", (PluginSpec("recovery", {"retry_budget": retry_budget}),)),),
    )


def _candidate() -> EvolutionCandidate:
    return EvolutionCandidate.from_profile(
        version_id="recovery-v2",
        base_version_id="baseline-v1",
        profile=_profile("candidate", 2),
        hypothesis="Bounded recovery improves completion without hiding failures.",
        component_changes=("recovery.retry_budget:1->2",),
        evidence_refs=("ledger://recovery-outcomes",),
    )


def _evaluation(**overrides: object) -> ShadowEvaluation:
    values = {
        "version_id": "recovery-v2",
        "matched_samples": 8,
        "quality_delta": 0.08,
        "quality_delta_lower_bound": 0.01,
        "token_delta_ratio": 0.04,
        "regressions": 0,
        "integrity_passed": True,
        "leakage_passed": True,
        "evaluation_ref": "evidence://paired-shadow-8",
    }
    values.update(overrides)
    return ShadowEvaluation(**values)  # type: ignore[arg-type]


class EvolutionTests(unittest.TestCase):
    def test_insufficient_evidence_keeps_candidate_in_shadow(self) -> None:
        ledger = SessionLedger("evolution-shadow")
        manager = EvolutionManager(ledger)
        manager.register_baseline("baseline-v1", _profile("baseline", 1))
        manager.create_candidate(_candidate())

        decision = manager.evaluate(_evaluation(matched_samples=1))

        self.assertEqual(decision.disposition, EvolutionDisposition.KEEP_SHADOW)
        self.assertEqual(manager.state.active_version_id, "baseline-v1")
        self.assertEqual(manager.state.versions["recovery-v2"].status, VersionStatus.SHADOW)
        self.assertEqual(
            manager.state.decisions["recovery-v2"].disposition,
            EvolutionDisposition.KEEP_SHADOW,
        )

    def test_promotion_and_rollback_are_append_only_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evolution.jsonl"
            ledger = SessionLedger("evolution-promote", path)
            manager = EvolutionManager(ledger)
            manager.register_baseline("baseline-v1", _profile("baseline", 1))
            manager.create_candidate(_candidate())

            decision = manager.evaluate(_evaluation())
            manager.rollback("baseline-v1", reason="post-promotion production regression")

            replayed = EvolutionManager(SessionLedger.replay(path)).state

        self.assertEqual(decision.disposition, EvolutionDisposition.PROMOTE)
        self.assertEqual(replayed.active_version_id, "baseline-v1")
        self.assertEqual(replayed.versions["recovery-v2"].status, VersionStatus.ROLLED_BACK)
        self.assertEqual(replayed.versions["baseline-v1"].status, VersionStatus.PROMOTED)

    def test_integrity_leakage_regression_quality_and_cost_fail_closed(self) -> None:
        cases = (
            {"integrity_passed": False},
            {"leakage_passed": False},
            {"regressions": 1},
            {"quality_delta_lower_bound": -0.01},
            {"token_delta_ratio": 0.11},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(overrides=overrides):
                manager = EvolutionManager(SessionLedger(f"evolution-reject-{index}"))
                manager.register_baseline("baseline-v1", _profile("baseline", 1))
                manager.create_candidate(_candidate())

                decision = manager.evaluate(_evaluation(**overrides))

                self.assertEqual(decision.disposition, EvolutionDisposition.REJECT)
                self.assertEqual(
                    manager.state.versions["recovery-v2"].status,
                    VersionStatus.REJECTED,
                )
                self.assertEqual(manager.state.active_version_id, "baseline-v1")

    def test_candidate_must_fork_active_profile_and_cite_evidence(self) -> None:
        manager = EvolutionManager(SessionLedger("evolution-invalid"))
        manager.register_baseline("baseline-v1", _profile("baseline", 1))
        invalid = EvolutionCandidate.from_profile(
            version_id="invalid-v2",
            base_version_id="unknown-v1",
            profile=_profile("candidate", 2),
            hypothesis="Try a change.",
            component_changes=("recovery",),
            evidence_refs=(),
        )

        with self.assertRaisesRegex(ValueError, "provenance evidence"):
            manager.create_candidate(invalid)


if __name__ == "__main__":
    unittest.main()
