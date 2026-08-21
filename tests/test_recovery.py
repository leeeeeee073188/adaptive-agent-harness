from __future__ import annotations

import unittest

from adaptive_harness.recovery import (
    RuleBasedTaskRecoveryPolicy,
    TaskFailureCategory,
    TaskFailureContext,
    TaskRecoveryAction,
)


class TaskRecoveryTests(unittest.TestCase):
    def test_browser_no_progress_refreshes_and_switches_without_raw_retry(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.BROWSER_GROUNDING,
                (TaskFailureCategory.NO_PROGRESS, TaskFailureCategory.LOOP),
                repeated_action_count=3,
            )
        )

        self.assertTrue(decision.should_continue)
        self.assertEqual(
            decision.actions,
            (
                TaskRecoveryAction.STOP_REPEATED_ACTION,
                TaskRecoveryAction.REFRESH_STATE,
                TaskRecoveryAction.SWITCH_TOOL,
            ),
        )

    def test_constraint_and_artifact_failures_validate_before_replan(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.ARTIFACT_ERROR,
                (TaskFailureCategory.CONSTRAINT_MISS, TaskFailureCategory.PREMATURE_FINISH),
            )
        )

        self.assertEqual(
            decision.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.WRITE_PARTIAL),
        )

    def test_exhausted_recovery_budget_stops(self) -> None:
        attempts = {action: 1 for action in TaskRecoveryAction if action is not TaskRecoveryAction.STOP}
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.BROWSER_GROUNDING,
                (TaskFailureCategory.NO_PROGRESS,),
                attempts=attempts,
            )
        )

        self.assertFalse(decision.should_continue)
        self.assertEqual(decision.actions, (TaskRecoveryAction.STOP,))


if __name__ == "__main__":
    unittest.main()
