from __future__ import annotations

import unittest

from adaptive_harness.experience_evolution import (
    DevelopmentRollout,
    ExperienceDraft,
    OfflineExperienceEvolution,
    RolloutPartition,
    TrajectoryLeakageError,
)
from adaptive_harness.experience_store import ExperienceStatus, ExperienceStore
from adaptive_harness.ledger import SessionLedger


class RecordingDistiller:
    def __init__(self) -> None:
        self.calls = []

    def distill(self, trigger, rollouts):
        self.calls.append((trigger, rollouts))
        return ExperienceDraft(
            experience_id="recover-timeout-with-narrower-scope",
            situation="A command repeatedly times out without producing task evidence.",
            strategy="Inspect partial state, narrow the operation, and retry once.",
            anti_pattern="Do not repeat the unchanged long-running operation.",
            progress_signal="The narrower operation produces new evidence or changed state.",
            stop_condition="Stop when the narrower operation also makes no progress.",
        )


def _rollout(task_id: str, score: float, **overrides: object) -> DevelopmentRollout:
    values = {
        "task_id": task_id,
        "partition": RolloutPartition.DEVELOPMENT,
        "trajectory_ref": f"ledger://{task_id}",
        "task_state": "tool_recovery",
        "failure_type": "TIMEOUT",
        "runtime_surface": "cli",
        "outcome_score": score,
        "passed": score == 1.0,
        "events": (
            {
                "type": "tool/result",
                "payload": {"error_type": "TIMEOUT", "result_changed": False},
            },
            {
                "type": "progress/checked",
                "payload": {"status": "no_progress"},
            },
        ),
    }
    values.update(overrides)
    return DevelopmentRollout(**values)  # type: ignore[arg-type]


class OfflineExperienceEvolutionTests(unittest.TestCase):
    def test_group_relative_rollouts_create_candidate_without_exposing_task_identity(self) -> None:
        store = ExperienceStore(SessionLedger("offline-evolution"))
        distiller = RecordingDistiller()
        evolution = OfflineExperienceEvolution(
            store,
            distiller,
            development_task_ids=("dev-a", "dev-b", "dev-c"),
        )

        created = evolution.distill_candidates(
            (_rollout("dev-a", 1.0), _rollout("dev-b", 0.0), _rollout("dev-c", 0.5))
        )

        self.assertEqual(len(created), 1)
        candidate = created[0]
        self.assertEqual(candidate.source_task_ids, ("dev-a", "dev-b", "dev-c"))
        self.assertEqual(store.state.lifecycle[candidate.key], ExperienceStatus.CANDIDATE)
        _, model_rollouts = distiller.calls[0]
        rendered = str(model_rollouts)
        self.assertNotIn("dev-a", rendered)
        self.assertNotIn("ledger://", rendered)

    def test_transfer_or_heldout_rollout_cannot_enter_distillation(self) -> None:
        store = ExperienceStore(SessionLedger("offline-partition"))
        evolution = OfflineExperienceEvolution(
            store,
            RecordingDistiller(),
            development_task_ids=("a", "b", "c"),
        )

        for partition in (RolloutPartition.TRANSFER, RolloutPartition.HELDOUT):
            with self.subTest(partition=partition), self.assertRaisesRegex(
                ValueError, "Development Rollouts"
            ):
                evolution.distill_candidates(
                    (
                        _rollout("a", 1.0, partition=partition),
                        _rollout("b", 0.0),
                        _rollout("c", 0.5),
                    )
                )

    def test_underpowered_or_homogeneous_group_does_not_spend_distillation_call(self) -> None:
        distiller = RecordingDistiller()
        evolution = OfflineExperienceEvolution(
            ExperienceStore(SessionLedger("offline-underpowered")),
            distiller,
            development_task_ids=("a", "b", "c"),
        )

        too_few = evolution.distill_candidates((_rollout("a", 1.0), _rollout("b", 0.0)))
        homogeneous = evolution.distill_candidates(
            (_rollout("a", 1.0), _rollout("b", 1.0), _rollout("c", 1.0))
        )

        self.assertEqual(too_few, ())
        self.assertEqual(homogeneous, ())
        self.assertEqual(distiller.calls, [])

    def test_verifier_or_expected_answer_data_fails_closed_before_distillation(self) -> None:
        distiller = RecordingDistiller()
        evolution = OfflineExperienceEvolution(
            ExperienceStore(SessionLedger("offline-leakage")),
            distiller,
            development_task_ids=("dev-a", "dev-b", "dev-c"),
        )
        leaked = _rollout(
            "dev-a",
            1.0,
            events=(
                {
                    "type": "tool/result",
                    "payload": {"expected_answer": "private"},
                },
            ),
        )

        with self.assertRaises(TrajectoryLeakageError):
            evolution.distill_candidates((leaked, _rollout("dev-b", 0.0), _rollout("dev-c", 0.5)))

        self.assertEqual(distiller.calls, [])

    def test_caller_cannot_relabel_heldout_task_as_development(self) -> None:
        evolution = OfflineExperienceEvolution(
            ExperienceStore(SessionLedger("offline-trusted-partition")),
            RecordingDistiller(),
            development_task_ids=("dev-a", "dev-b", "dev-c"),
        )

        with self.assertRaisesRegex(ValueError, "trusted Development partition"):
            evolution.distill_candidates(
                (
                    _rollout("heldout-a", 1.0),
                    _rollout("dev-b", 0.0),
                    _rollout("dev-c", 0.5),
                )
            )

    def test_camelcase_verifier_fields_and_selectors_fail_closed(self) -> None:
        risky_payloads = (
            {"expectedAnswer": "private"},
            {"public_expected_answer": "private"},
            {"verifier_private_feedback": "private"},
            {"judge": {"reasoning": "private"}},
            {"content": "Click #submit-answer."},
            {"content": "Use [data-answer=value]."},
            {"content": "browser-other-task"},
            {"content": "expected output is private"},
            {"content": "gold-answer is private"},
            {"content": "rubric says pass"},
            {"content": "verifier says correct"},
            {"content": "expected_answer private"},
            {"content": "ground_truth private"},
            {"content": "answer_key private"},
        )
        for index, payload in enumerate(risky_payloads):
            with self.subTest(payload=payload):
                evolution = OfflineExperienceEvolution(
                    ExperienceStore(SessionLedger(f"offline-risk-{index}")),
                    RecordingDistiller(),
                    development_task_ids=("dev-a", "dev-b", "dev-c"),
                )
                leaked = _rollout(
                    "dev-a",
                    1.0,
                    events=(({"type": "tool/result", "payload": payload}),),
                )

                with self.assertRaises(TrajectoryLeakageError):
                    evolution.distill_candidates(
                        (leaked, _rollout("dev-b", 0.0), _rollout("dev-c", 0.5))
                    )

    def test_failed_later_group_does_not_partially_admit_earlier_candidate(self) -> None:
        store = ExperienceStore(SessionLedger("offline-atomic"))
        evolution = OfflineExperienceEvolution(
            store,
            RecordingDistiller(),
            development_task_ids=("a1", "a2", "a3", "z1", "z2", "z3"),
        )
        safe = tuple(
            _rollout(task_id, score, task_state="a_state")
            for task_id, score in (("a1", 1.0), ("a2", 0.0), ("a3", 0.5))
        )
        leaked = tuple(
            _rollout(
                task_id,
                score,
                task_state="z_state",
                events=(
                    {
                        "type": "tool/result",
                        "payload": {"groundTruth": "private"},
                    },
                ),
            )
            for task_id, score in (("z1", 1.0), ("z2", 0.0), ("z3", 0.5))
        )

        with self.assertRaises(TrajectoryLeakageError):
            evolution.distill_candidates((*safe, *leaked))

        self.assertEqual(store.state.experiences, {})


if __name__ == "__main__":
    unittest.main()
