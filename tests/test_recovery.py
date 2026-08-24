from __future__ import annotations

import unittest

from adaptive_harness.recovery import (
    RecoveryActionEffect,
    RecoveryExecution,
    RuleBasedRecoveryOutcomeEvaluator,
    RuleBasedTaskRecoveryExecutor,
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

    def test_invalid_artifact_has_one_independent_repair_attempt(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.ARTIFACT_INVALID,
                attempts={
                    TaskRecoveryAction.VALIDATE_CONTRACT: 1,
                    TaskRecoveryAction.WRITE_PARTIAL: 1,
                },
            )
        )

        self.assertTrue(decision.should_continue)
        self.assertEqual(decision.actions, (TaskRecoveryAction.REPAIR_ARTIFACT,))
        execution = RuleBasedTaskRecoveryExecutor().execute(
            decision,
            missing=("Artifact failed public shape validation",),
        )
        self.assertTrue(execution.state_delta["recovery.partial_delivery_requested"])
        self.assertIn("existing artifact failed public validation", execution.directives[0])

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


    def test_evidence_gap_validates_and_replans_without_delivery_action(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.EVIDENCE_GAP,
                (TaskFailureCategory.PREMATURE_FINISH,),
            )
        )

        self.assertTrue(decision.should_continue)
        self.assertEqual(
            decision.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN),
        )
        self.assertNotIn(TaskRecoveryAction.WRITE_PARTIAL, decision.actions)

    def test_evidence_gap_loop_can_stop_repeated_action_without_delivery(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.EVIDENCE_GAP,
                (TaskFailureCategory.LOOP,),
                repeated_action_count=3,
            )
        )

        self.assertEqual(
            decision.actions,
            (
                TaskRecoveryAction.STOP_REPEATED_ACTION,
                TaskRecoveryAction.VALIDATE_CONTRACT,
                TaskRecoveryAction.REPLAN,
            ),
        )
        self.assertNotIn(TaskRecoveryAction.WRITE_PARTIAL, decision.actions)

    def test_synthesis_lineage_gap_validates_and_replans_without_rewriting_placeholder(self) -> None:
        decision = RuleBasedTaskRecoveryPolicy().decide(
            TaskFailureContext(
                TaskFailureCategory.SYNTHESIS_LINEAGE_GAP,
                (TaskFailureCategory.PREMATURE_FINISH,),
            )
        )

        self.assertEqual(
            decision.actions,
            (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN),
        )
        self.assertNotIn(TaskRecoveryAction.WRITE_PARTIAL, decision.actions)

    def test_executor_applies_control_state_and_directives(self) -> None:
        execution = RuleBasedTaskRecoveryExecutor().execute(
            RuleBasedTaskRecoveryPolicy().decide(
                TaskFailureContext(TaskFailureCategory.ARTIFACT_ERROR)
            ),
            missing=("Missing artifact evidence: outputs/report.csv",),
        )

        self.assertEqual(
            execution.state_delta["recovery.missing_requirements"],
            ["Missing artifact evidence: outputs/report.csv"],
        )
        self.assertTrue(execution.state_delta["recovery.partial_delivery_requested"])
        self.assertEqual(len(execution.directives), 2)
        self.assertIn("[HARNESS DELIVERY REQUIRED]", execution.directives[1])
        self.assertIn("outputs/report.csv", execution.directives[1])
        self.assertIn("directly synthesize", execution.directives[1])
        self.assertIn("empty placeholder", execution.directives[1])

    def test_outcome_requires_semantic_progress_or_completion(self) -> None:
        execution = RecoveryExecution(
            (
                RecoveryActionEffect(
                    TaskRecoveryAction.REFRESH_STATE,
                    "applied",
                    {},
                    "refresh",
                ),
            )
        )
        evaluator = RuleBasedRecoveryOutcomeEvaluator()

        failed = evaluator.evaluate(
            execution_seq=3,
            execution=execution,
            progress_status="no_progress",
            completion_passed=False,
        )
        passed = evaluator.evaluate(
            execution_seq=3,
            execution=execution,
            progress_status="progressed",
            completion_passed=False,
        )

        self.assertFalse(failed.effective)
        self.assertTrue(passed.effective)


if __name__ == "__main__":
    unittest.main()
