from __future__ import annotations

import unittest

from adaptive_harness.experience_evolution import OfflineExperienceEvolution, RolloutPartition, TrajectoryLeakageError
from adaptive_harness.experience_store import ExperienceStore
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.ledger_rollout import PublicOutcome, ledger_to_development_rollout


class RecordingDistiller:
    def __init__(self) -> None:
        self.calls = []

    def distill(self, trigger, rollouts):  # pragma: no cover - converter compatibility smoke only
        from adaptive_harness.experience_evolution import ExperienceDraft

        self.calls.append((trigger, rollouts))
        return ExperienceDraft(
            experience_id="recover-runtime-error-with-public-evidence",
            situation="A runtime action fails after public tool evidence is collected.",
            strategy="Classify the failure and retry only after a recovery action changes state.",
            anti_pattern="Do not use verifier, rubric, or expected-answer details for recovery.",
            progress_signal="A public progress check or recovery outcome changes state.",
            stop_condition="Stop when public completion passes or recovery is ineffective.",
        )


def _ledger(task_id: str = "dev-a", *, partition: str = "development") -> SessionLedger:
    ledger = SessionLedger("run-a")
    ledger.append("runtime/start", {"runtime": "agent-driver"})
    ledger.append(
        "state/updated",
        {
            "delta": {
                "task_id": task_id,
                "partition": partition,
                "task_state": "tool_recovery",
                "runtime_surface": "cli",
            },
            "reason": "public runtime identity",
        },
    )
    ledger.append("assistant/message", {"content": "I will inspect public state.", "usage": {"total_tokens": 12}})
    ledger.append("tool/call", {"call_id": "call-1", "name": "shell", "arguments": {"command": "pytest tests"}})
    ledger.append(
        "tool/result",
        {
            "call_id": "call-1",
            "content": "timeout after public command",
            "error_type": "TIMEOUT",
            "metadata": {"attempts": 1},
        },
    )
    ledger.append(
        "failure/classified",
        {
            "failure": {
                "id": "call-1:attempt:1",
                "error_type": "TIMEOUT",
                "message": "command timed out",
                "source": "tool_result",
                "metadata": {"attempt": 1, "retryable": True},
            }
        },
    )
    ledger.append("progress/checked", {"status": "no_progress", "changed": False})
    ledger.append("completion/checked", {"passed": False, "feedback": "public completion rejected"})
    ledger.append("runtime/end", {"reason": "completion_rejected"})
    return ledger


class LedgerRolloutConversionTests(unittest.TestCase):
    def test_converts_public_runtime_events_into_development_rollout_for_existing_evolution(self) -> None:
        rollout = ledger_to_development_rollout(
            _ledger(),
            trusted_development_task_ids={"dev-a", "dev-b", "dev-c"},
            outcome=PublicOutcome(score=0.25, passed=False),
        )

        self.assertEqual(rollout.task_id, "dev-a")
        self.assertIs(rollout.partition, RolloutPartition.DEVELOPMENT)
        self.assertEqual(rollout.task_state, "tool_recovery")
        self.assertEqual(rollout.failure_type, "TIMEOUT")
        self.assertEqual(rollout.runtime_surface, "cli")
        self.assertEqual(rollout.outcome_score, 0.25)
        self.assertFalse(rollout.passed)
        self.assertTrue(rollout.trajectory_ref.startswith("ledger-rollout-sha256:"))
        self.assertNotIn("dev-a", str(rollout.events))
        self.assertNotIn("usage", str(rollout.events))

        evolution = OfflineExperienceEvolution(
            ExperienceStore(SessionLedger("compat")),
            RecordingDistiller(),
            development_task_ids=("dev-a", "dev-b", "dev-c"),
        )
        self.assertEqual(evolution.distill_candidates((rollout,)), ())

    def test_fingerprint_is_stable_across_replayed_equivalent_ledgers(self) -> None:
        first = ledger_to_development_rollout(
            _ledger(),
            trusted_development_task_ids={"dev-a"},
            outcome={"score": 1, "passed": True},
        )
        second = ledger_to_development_rollout(
            _ledger(),
            trusted_development_task_ids={"dev-a"},
            outcome={"score": 1.0, "passed": True},
        )

        self.assertEqual(first.trajectory_ref, second.trajectory_ref)
        self.assertEqual(first.events, second.events)

    def test_rejects_task_outside_trusted_development_set_even_if_labeled_development(self) -> None:
        with self.assertRaisesRegex(ValueError, "trusted Development"):
            ledger_to_development_rollout(
                _ledger("transfer-a", partition="development"),
                trusted_development_task_ids={"dev-a"},
                outcome=PublicOutcome(score=0.0, passed=False),
            )

    def test_rejects_ledger_partition_claiming_transfer_or_heldout(self) -> None:
        for partition in ("transfer", "heldout"):
            with self.subTest(partition=partition), self.assertRaisesRegex(ValueError, "Development partition"):
                ledger_to_development_rollout(
                    _ledger("dev-a", partition=partition),
                    trusted_development_task_ids={"dev-a"},
                    outcome=PublicOutcome(score=0.0, passed=False),
                )

    def test_rejects_disallowed_events_and_evaluation_only_fields(self) -> None:
        cases = (
            ("judge/result", {"score": 1.0}),
            ("tool/result", {"expectedAnswer": "private"}),
            ("tool/result", {"rubric": "private"}),
            ("tool/result", {"content": "the expected answer is private"}),
            ("assistant/message", {"content": "Click #submit-answer"}),
            ("tool/call", {"arguments": {"selector": "[data-answer=value]"}}),
            ("tool/result", {"content": "mentions transfer-a"}),
            ("tool/result", {"taskId": "heldout-a"}),
        )
        for index, (event_type, payload) in enumerate(cases):
            with self.subTest(event_type=event_type, payload=payload):
                ledger = _ledger()
                ledger.append(event_type, payload)
                with self.assertRaises(TrajectoryLeakageError):
                    ledger_to_development_rollout(
                        ledger,
                        trusted_development_task_ids={"dev-a"},
                        blocked_task_ids={"transfer-a", "heldout-a"},
                        outcome=PublicOutcome(score=0.0, passed=False),
                    )

    def test_requires_explicit_public_outcome_and_valid_score(self) -> None:
        with self.assertRaisesRegex(ValueError, "public outcome"):
            ledger_to_development_rollout(_ledger(), trusted_development_task_ids={"dev-a"}, outcome={})
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            ledger_to_development_rollout(
                _ledger(),
                trusted_development_task_ids={"dev-a"},
                outcome=PublicOutcome(score=1.5, passed=True),
            )

    def test_runtime_error_only_ledger_becomes_failed_development_rollout(self) -> None:
        ledger = SessionLedger("runtime-error-only")
        ledger.append("runtime/start", {"runtime": "deerflow"})
        ledger.append(
            "state/updated",
            {
                "delta": {
                    "task_id": "dev-a",
                    "partition": "development",
                    "task_state": "runtime_failed",
                    "runtime_surface": "browser",
                },
                "reason": "public runtime identity",
            },
        )
        ledger.append(
            "runtime/error",
            {"type": "RuntimeError", "message": "public stream failed"},
        )
        ledger.append("runtime/end", {"reason": "runtime_error"})

        rollout = ledger_to_development_rollout(
            ledger,
            trusted_development_task_ids={"dev-a"},
            outcome=PublicOutcome(0.0, False),
        )

        self.assertEqual(rollout.failure_type, "RUNTIME_ERROR:RuntimeError")
        self.assertEqual([event["type"] for event in rollout.events], ["runtime/error"])


if __name__ == "__main__":
    unittest.main()
