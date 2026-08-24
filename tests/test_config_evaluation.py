from __future__ import annotations

import unittest

from adaptive_harness.config import Bundle, PluginSpec, Profile
from adaptive_harness.evaluation import ExperienceAdmissibilityFilter, ExperienceCandidate


class ConfigEvaluationTests(unittest.TestCase):
    def test_profile_overlay_replaces_bundle_row_and_fingerprint_is_stable(self) -> None:
        profile = Profile(
            "candidate",
            (Bundle("base", (PluginSpec("model", {"name": "m"}), PluginSpec("guard"))),),
            (PluginSpec("guard", {"enabled": False}, enabled=False),),
        )
        same = Profile(
            "candidate",
            (Bundle("base", (PluginSpec("model", {"name": "m"}), PluginSpec("guard"))),),
            (PluginSpec("guard", {"enabled": False}, enabled=False),),
        )

        self.assertEqual([item.name for item in profile.compose()], ["model"])
        self.assertEqual(profile.fingerprint(), same.fingerprint())

    def test_experience_filter_rejects_benchmark_identity_and_verifier_language(self) -> None:
        filter_ = ExperienceAdmissibilityFilter()
        safe = ExperienceCandidate(
            "A dynamic form changed after input.",
            "Refresh observable state before locating the next control.",
            "Do not reuse stale element identities.",
            ("run-1",),
        )
        leaked = ExperienceCandidate(
            "browser-secret-task",
            "Read the verifier expected answer.",
            "",
            ("run-2",),
        )
        metadata_leak = ExperienceCandidate(
            "A generic task failed.",
            "Retry with a safer tool.",
            "",
            ("run-3",),
            {"expected_answer": "private"},
        )

        self.assertTrue(filter_.admit(safe))
        self.assertFalse(filter_.admit(leaked))
        self.assertFalse(filter_.admit(metadata_leak))
