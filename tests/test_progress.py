from __future__ import annotations

import unittest

from adaptive_harness.progress import ProgressStatus, RuleBasedProgressDetector
from adaptive_harness.task_state import Evidence, EvidenceKind, EvidenceSource, TaskState


class ProgressDetectorTests(unittest.TestCase):
    def test_repeated_negative_evidence_is_not_progress(self) -> None:
        detector = RuleBasedProgressDetector()
        before = TaskState(
            evidence=(
                Evidence(
                    "e1",
                    EvidenceKind.ARTIFACT,
                    "outputs/report.csv",
                    {"exists": False},
                    EvidenceSource.ARTIFACT_INSPECTION,
                ),
            )
        )
        after = TaskState(
            evidence=(
                *before.evidence,
                Evidence(
                    "e2",
                    EvidenceKind.ARTIFACT,
                    "outputs/report.csv",
                    {"exists": False},
                    EvidenceSource.ARTIFACT_INSPECTION,
                ),
            )
        )

        result = detector.detect(detector.snapshot(before), detector.snapshot(after))

        self.assertEqual(result.status, ProgressStatus.NO_PROGRESS)

    def test_negative_to_positive_evidence_is_progress(self) -> None:
        detector = RuleBasedProgressDetector()
        before = TaskState(
            evidence=(
                Evidence(
                    "e1",
                    EvidenceKind.OBSERVATION,
                    "listing.submitted",
                    False,
                    EvidenceSource.RUNTIME_OBSERVATION,
                ),
            )
        )
        after = TaskState(
            evidence=(
                *before.evidence,
                Evidence(
                    "e2",
                    EvidenceKind.OBSERVATION,
                    "listing.submitted",
                    True,
                    EvidenceSource.RUNTIME_OBSERVATION,
                ),
            )
        )

        result = detector.detect(detector.snapshot(before), detector.snapshot(after))

        self.assertEqual(result.status, ProgressStatus.PROGRESSED)
        self.assertEqual(result.changed_evidence, ("observation:listing.submitted",))

    def test_recovery_control_flags_do_not_count_as_task_progress(self) -> None:
        detector = RuleBasedProgressDetector()
        before = TaskState(values={})
        after = TaskState(
            values={
                "recovery.replan_requested": True,
                "progress.last_status": "no_progress",
            }
        )

        result = detector.detect(detector.snapshot(before), detector.snapshot(after))

        self.assertEqual(result.status, ProgressStatus.NO_PROGRESS)


if __name__ == "__main__":
    unittest.main()
