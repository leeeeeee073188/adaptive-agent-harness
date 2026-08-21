from __future__ import annotations

import unittest

from adaptive_harness.recovery import RecoveryOutcome, TaskRecoveryAction
from adaptive_harness.recovery_practice import RecoveryOutcomeAggregator, RecoveryPracticeGate


class RecoveryPracticeTests(unittest.TestCase):
    def test_five_isolated_successes_pass_confidence_gate(self) -> None:
        stats = RecoveryOutcomeAggregator().aggregate(
            _outcomes(TaskRecoveryAction.REFRESH_STATE, [True] * 5)
        )[0]

        decision = RecoveryPracticeGate().evaluate(stats)

        self.assertTrue(decision.eligible)
        self.assertGreater(stats.wilson_lower or 0, 0.3)

    def test_too_few_successes_remain_ineligible(self) -> None:
        stats = RecoveryOutcomeAggregator().aggregate(
            _outcomes(TaskRecoveryAction.REPLAN, [True] * 3)
        )[0]

        decision = RecoveryPracticeGate().evaluate(stats)

        self.assertFalse(decision.eligible)
        self.assertIn("Insufficient", decision.reason)

    def test_nominal_sixty_percent_fails_wilson_lower_bound(self) -> None:
        stats = RecoveryOutcomeAggregator().aggregate(
            _outcomes(TaskRecoveryAction.SWITCH_TOOL, [True, True, True, False, False])
        )[0]

        decision = RecoveryPracticeGate().evaluate(stats)

        self.assertEqual(stats.effective_rate, 0.6)
        self.assertFalse(decision.eligible)
        self.assertLess(stats.wilson_lower or 1, 0.3)

    def test_multi_action_batches_are_visible_but_confounded(self) -> None:
        outcomes = [
            RecoveryOutcome(
                index,
                (TaskRecoveryAction.REFRESH_STATE, TaskRecoveryAction.SWITCH_TOOL),
                "progressed",
                False,
                True,
                "",
            )
            for index in range(10)
        ]
        stats = RecoveryOutcomeAggregator().aggregate(outcomes)

        self.assertTrue(all(row.total_samples == 10 for row in stats))
        self.assertTrue(all(row.confounded_samples == 10 for row in stats))
        self.assertTrue(all(row.isolated_samples == 0 for row in stats))
        self.assertTrue(all(not RecoveryPracticeGate().evaluate(row).eligible for row in stats))


def _outcomes(
    action: TaskRecoveryAction,
    effective: list[bool],
) -> list[RecoveryOutcome]:
    return [
        RecoveryOutcome(index, (action,), "progressed" if value else "no_progress", False, value, "")
        for index, value in enumerate(effective)
    ]


if __name__ == "__main__":
    unittest.main()
