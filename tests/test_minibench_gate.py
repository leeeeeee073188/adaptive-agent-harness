from __future__ import annotations

import unittest

from adaptive_harness.integrations.minibench_gate import (
    PaidCanaryEvidence,
    decide_paid_canary,
    evaluate_partition_integrity,
)
from adaptive_harness.integrations.realreplica import (
    EvaluationRole,
    MiniBenchDataset,
    MiniBenchTask,
)


def _task(
    index: int,
    role: EvaluationRole,
    *,
    difficulty: str,
    capability: str,
) -> MiniBenchTask:
    categories = ("file", "browser", "cli", "api")
    return MiniBenchTask(
        task_id=f"sample-{index}",
        category=categories[index % 4],
        difficulty_raw=difficulty,
        difficulty_band=difficulty,
        capability=capability,
        evaluation_role=role,
        block=index // 4 + 1,
        max_actions=60,
        timeout_sec=1200,
        language="en-US",
        prompt="Do a public task.",
        prompt_sha256=f"hash-{index}",
        smoke_reuse=index < 3,
    )


def _dataset() -> MiniBenchDataset:
    rows = []
    role_counts = (
        (EvaluationRole.DEVELOPMENT, 8),
        (EvaluationRole.TRANSFER, 4),
        (EvaluationRole.HELDOUT, 4),
    )
    index = 0
    for role, count in role_counts:
        for offset in range(count):
            rows.append(
                _task(
                    index,
                    role,
                    difficulty="easy" if offset == 0 else "hard",
                    capability="vision" if offset == 1 else "text-only",
                )
            )
            index += 1
    return MiniBenchDataset("fixture", 7, tuple(rows), {}, "fingerprint")


class MiniBenchGateTests(unittest.TestCase):
    def test_partition_integrity_requires_8_4_4_and_diversity_per_role(self) -> None:
        report = evaluate_partition_integrity(_dataset())

        self.assertTrue(report.passed, report.reasons)
        self.assertEqual(
            report.role_counts,
            {"development": 8, "transfer": 4, "heldout": 4},
        )

    def test_partition_integrity_rejects_difficulty_or_capability_collapse(self) -> None:
        dataset = _dataset()
        broken = MiniBenchDataset(
            dataset.dataset_id,
            dataset.seed,
            tuple(
                _task(
                    index,
                    task.evaluation_role,
                    difficulty=(
                        "hard"
                        if task.evaluation_role is EvaluationRole.TRANSFER
                        else task.difficulty_band
                    ),
                    capability=(
                        "text-only"
                        if task.evaluation_role is EvaluationRole.TRANSFER
                        else task.capability
                    ),
                )
                for index, task in enumerate(dataset.tasks)
            ),
            dataset.block_purposes,
            dataset.fingerprint,
        )

        report = evaluate_partition_integrity(broken)

        self.assertFalse(report.passed)
        self.assertTrue(any("transfer" in reason for reason in report.reasons))

    def test_paid_development_canary_fails_closed_until_every_gate_passes(self) -> None:
        incomplete = PaidCanaryEvidence(
            partition_integrity_passed=True,
            runtime_conformance_passed=True,
            container_wiring_passed=False,
            historical_baseline_available=False,
            stable_regression_controls_passed=True,
            profile_reproducible=True,
            credentials_external=True,
            single_canary_default=True,
        )
        complete = PaidCanaryEvidence(
            partition_integrity_passed=True,
            runtime_conformance_passed=True,
            container_wiring_passed=True,
            historical_baseline_available=True,
            stable_regression_controls_passed=True,
            profile_reproducible=True,
            credentials_external=True,
            single_canary_default=True,
        )

        self.assertFalse(decide_paid_canary(incomplete).allowed)
        self.assertEqual(len(decide_paid_canary(incomplete).blockers), 2)
        self.assertTrue(decide_paid_canary(complete).allowed)

    def test_heldout_is_never_authorized_by_generic_canary_gate(self) -> None:
        evidence = PaidCanaryEvidence(
            partition_integrity_passed=True,
            runtime_conformance_passed=True,
            container_wiring_passed=True,
            historical_baseline_available=True,
            stable_regression_controls_passed=True,
            profile_reproducible=True,
            credentials_external=True,
            single_canary_default=True,
            target_role=EvaluationRole.HELDOUT,
        )

        decision = decide_paid_canary(evidence)

        self.assertFalse(decision.allowed)
        self.assertIn("final Candidate", decision.blockers[0])


if __name__ == "__main__":
    unittest.main()
