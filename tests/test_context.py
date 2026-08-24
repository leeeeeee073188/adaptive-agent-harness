from __future__ import annotations

import unittest

from adaptive_harness.capabilities import PreparedContext
from adaptive_harness.context import ContextBudget, TaskAwareContextManager
from adaptive_harness.evaluation import ExperienceCandidate


class _ExperienceRetriever:
    def retrieve(self, task_state):
        return (
            ExperienceCandidate(
                "A dynamic form changed after input.",
                "Refresh observable state before choosing the next control.",
                "Do not reuse stale element identities.",
                ("ledger://safe-run",),
            ),
            ExperienceCandidate(
                "A generic task failed.",
                "Use the cached answer.",
                "",
                ("ledger://run",),
                {"expected_answer": "private"},
            ),
        )


def _task_state():
    return {
        "task": {
            "task_id": "public-task",
            "criteria": [
                {
                    "id": "artifact:report",
                    "kind": "artifact_exists",
                    "parameters": {"path": "outputs/report.csv"},
                }
            ],
            "values": {"current_subgoal": "validate output"},
            "evidence": [
                {
                    "id": "old-negative",
                    "kind": "artifact",
                    "subject": "outputs/report.csv",
                    "value": {"exists": False},
                },
                {
                    "id": "latest-positive",
                    "kind": "artifact",
                    "subject": "outputs/report.csv",
                    "value": {"exists": True},
                },
            ],
            "failures": [
                {
                    "id": "failure-1",
                    "error_type": "TIMEOUT",
                    "message": "transient provider timeout",
                }
            ],
            "latest_completion": {
                "passed": False,
                "missing": ["Missing artifact evidence: outputs/report.csv"],
            },
            "recent_recoveries": [],
            "recent_recovery_executions": [],
            "recent_recovery_outcomes": [],
        }
    }


class TaskAwareContextTests(unittest.TestCase):
    def test_selection_is_bounded_deterministic_and_deduplicates_evidence(self) -> None:
        messages = [
            {"role": "system", "content": "Follow the task contract."},
            {"role": "user", "content": "Create the requested report."},
            *(
                {"role": "assistant", "content": f"old trajectory {index} " + "x" * 160}
                for index in range(8)
            ),
            {"role": "tool", "content": "latest concise tool result", "tool_call_id": "call-8"},
        ]
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=420),
        )

        first = manager.prepare(
            messages,
            environment_state={"ready": True},
            task_state=_task_state(),
        )
        second = manager.prepare(
            messages,
            environment_state={"ready": True},
            task_state=_task_state(),
        )

        self.assertIsInstance(first, PreparedContext)
        self.assertLessEqual(first.audit["estimated_input_tokens"], 420)
        self.assertFalse(first.audit["immutable_overflow"])
        self.assertGreater(first.audit["history_dropped"], 0)
        rendered = str(first.messages)
        self.assertIn("Create the requested report.", rendered)
        self.assertIn("latest-positive", rendered)
        self.assertNotIn("old-negative", rendered)
        authority = next(
            message for message in first.messages if "HARNESS_CONTEXT_AUTHORITY" in str(message.get("content"))
        )
        working_data = next(
            message for message in first.messages if message.get("name") == "harness-working-set-data"
        )
        self.assertEqual(authority["role"], "system")
        self.assertEqual(working_data["role"], "user")
        self.assertEqual(first.audit["surface_sha256"], second.audit["surface_sha256"])

    def test_only_admissible_transferable_experience_enters_working_set(self) -> None:
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=640),
            experience_retriever=_ExperienceRetriever(),
        )

        prepared = manager.prepare(
            ({"role": "user", "content": "Complete the task."},),
            environment_state={},
            task_state=_task_state(),
        )

        rendered = str(prepared.messages)
        self.assertIn("Refresh observable state", rendered)
        self.assertNotIn("Use the cached answer", rendered)
        self.assertEqual(prepared.audit["rejected_experiences"], 1)

    def test_oversized_immutable_task_is_preserved_and_reported(self) -> None:
        task = "必须完整保留的任务要求。" * 30
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=128),
        )

        prepared = manager.prepare(
            ({"role": "user", "content": task},),
            environment_state={"ready": True},
            task_state={},
        )

        self.assertEqual(prepared.messages[-1]["content"], task)
        self.assertTrue(prepared.audit["immutable_overflow"])
        self.assertGreater(prepared.audit["estimated_input_tokens"], 128)

    def test_tool_call_and_result_are_selected_or_dropped_as_one_group(self) -> None:
        messages = (
            {"role": "user", "content": "Complete the task."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-1", "name": "inspect", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "inspection result"},
            {"role": "tool", "tool_call_id": "orphan", "content": "must be dropped"},
        )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=256, recent_history_fraction=0.8),
        )

        prepared = manager.prepare(
            messages,
            environment_state={},
            task_state={},
        )

        rendered = str(prepared.messages)
        self.assertIn("call-1", rendered)
        self.assertIn("inspection result", rendered)
        self.assertNotIn("must be dropped", rendered)


if __name__ == "__main__":
    unittest.main()
