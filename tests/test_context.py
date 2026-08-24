from __future__ import annotations

import unittest

from adaptive_harness.capabilities import PreparedContext
from adaptive_harness.context import ContextBudget, TaskAwareContextManager
from adaptive_harness.evaluation import ExperienceCandidate
from adaptive_harness.evidence_workspace import EvidenceWorkspace
from adaptive_harness.experience_store import (
    Experience,
    ExperienceStore,
    TaskStateExperienceRetriever,
    TransferValidation,
)
from adaptive_harness.ledger import SessionLedger


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

    def test_promoted_store_experience_is_retrieved_without_source_task_ids(self) -> None:
        store = ExperienceStore(SessionLedger("context-experience-store"))
        experience = Experience(
            experience_id="recover-timeout",
            version=1,
            task_state="tool_recovery",
            failure_type="TIMEOUT",
            runtime_surface="cli",
            situation="A command timed out before producing task evidence.",
            strategy="Use a narrower command and inspect fresh evidence.",
            anti_pattern="Do not repeat the unchanged long-running command.",
            progress_signal="The narrower command produces new task evidence.",
            stop_condition="Stop after another no-progress outcome.",
            source_task_ids=("dev-a", "dev-b", "dev-c"),
        )
        store.create_candidate(experience)
        store.start_shadow(experience.experience_id, experience.version, reason="checks passed")
        store.promote(
            experience.experience_id,
            experience.version,
            TransferValidation(
                ("transfer-a", "transfer-b"),
                stable_pass_regressions=0,
                harm_observed=False,
                evidence_ref="evidence://transfer",
                effective_task_ids=("transfer-a",),
            ),
        )
        state = _task_state()
        state["task"]["values"].update(
            {
                "task_state": "tool_recovery",
                "failure_type": "TIMEOUT",
                "runtime_surface": "cli",
            }
        )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=900),
            experience_retriever=TaskStateExperienceRetriever(store),
        )

        prepared = manager.prepare(
            ({"role": "user", "content": "Complete the task."},),
            environment_state={},
            task_state=state,
        )

        rendered = str(prepared.messages)
        self.assertIn("new task evidence", rendered)
        self.assertIn("structured task state", rendered)
        self.assertNotIn("recover-timeout", rendered)
        self.assertNotIn("dev-a", rendered)

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

    def test_dropped_large_tool_results_leave_deduplicated_safe_interaction_facts(self) -> None:
        messages = (
            {"role": "user", "content": "Complete the task."},
            {
                "role": "assistant",
                "content": "Reading source.",
                "tool_calls": [
                    {
                        "id": "read-1",
                        "name": "read_file",
                        "arguments": {
                            "path": "/task/input.json",
                            "api_key": "sk-not-safe-123456",
                            "description": "First wording",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "read-1", "content": "x" * 5000},
            {
                "role": "assistant",
                "content": "Reading again.",
                "tool_calls": [
                    {
                        "id": "read-2",
                        "name": "read_file",
                        "arguments": {
                            "path": "/task/input.json",
                            "api_key": "sk-not-safe-123456",
                            "description": "Different wording",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "read-2", "content": "x" * 5000},
        )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=1024),
        )

        prepared = manager.prepare(
            messages,
            environment_state={},
            task_state={},
        )

        rendered = str(prepared.messages)
        self.assertIn("action:read:", rendered)
        self.assertIn('"attempts":2', rendered)
        self.assertIn('"distinct_results":1', rendered)
        self.assertIn('"repeated_unchanged":true', rendered)
        self.assertIn("explicit no-progress signal", rendered)
        self.assertNotIn("First wording", rendered)
        self.assertNotIn("Different wording", rendered)
        self.assertIn('"resources":["input.json"]', rendered)
        self.assertIn('"redacted_argument_keys":["api_key"]', rendered)
        self.assertNotIn("sk-not-safe", rendered)
        self.assertLess(len(rendered), 5000)

    def test_prepared_context_scrubs_tool_content_and_tool_call_arguments(self) -> None:
        messages = (
            {"role": "system", "content": "System keeps token budget."},
            {"role": "user", "content": "Complete task with token budget discipline."},
            {
                "role": "assistant",
                "content": "Reading config.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "arguments": {
                            "path": "/task/config.txt",
                            "password": "plainpass",
                            "nested": {"client_secret": "plain-client-secret"},
                            "url": "https://user:pass@example.com/a?api_key=hidden&key=public#frag",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "api_key=plain-api-key\nTITLE: token budget remains safe",
            },
        )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=2048, recent_history_fraction=0.8),
        )

        prepared = manager.prepare(messages, environment_state={}, task_state={})

        rendered = str(prepared.messages).lower()
        self.assertNotIn("plainpass", rendered)
        self.assertNotIn("plain-client-secret", rendered)
        self.assertNotIn("plain-api-key", rendered)
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("api_key=hidden", rendered)
        self.assertNotIn("frag", rendered)
        self.assertIn("token budget", rendered)
        tool_messages = [m for m in prepared.messages if m.get("role") == "tool"]
        assistant_messages = [m for m in prepared.messages if m.get("role") == "assistant"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call-1")
        self.assertEqual(assistant_messages[0]["tool_calls"][0]["id"], "call-1")

    def test_visible_workspace_keeps_key_evidence_when_large_history_is_dropped(self) -> None:
        large_result = "\n".join(
            [
                "noise " + "x" * 1800,
                "REQUIRED COLUMNS: week, visits, quality_score",
                "ERROR: null trend rows must be excluded",
                "noise " + "y" * 1800,
            ]
        )
        messages = (
            {"role": "system", "content": "Follow the task contract."},
            {"role": "user", "content": "Audit trend data and report required columns."},
            {
                "role": "assistant",
                "content": "Reading audit input.",
                "tool_calls": [
                    {
                        "id": "read-1",
                        "name": "read_file",
                        "arguments": {"path": "/task/workspace/audit.txt"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "read-1", "content": large_result},
            *(
                {"role": "assistant", "content": f"older message {index} " + "z" * 400}
                for index in range(10)
            ),
        )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=760, recent_history_fraction=0.1),
        )

        prepared = manager.prepare(
            messages,
            environment_state={},
            task_state={},
        )

        rendered = str(prepared.messages)
        self.assertGreater(prepared.audit["history_dropped"], 0)
        self.assertTrue(prepared.audit["visible_evidence_workspace"]["block_selected"])
        self.assertEqual(prepared.audit["visible_evidence_workspace"]["archived_resources"], 1)
        self.assertIn("visible_evidence_workspace", rendered)
        self.assertIn("workspace/audit.txt", rendered)
        self.assertIn("REQUIRED COLUMNS", rendered)
        self.assertIn("null trend rows", rendered)
        self.assertNotIn("noise " + "x" * 400, rendered)

    def test_visible_workspace_low_budget_keeps_dashboard_and_one_key_excerpt(self) -> None:
        messages: list[dict] = [
            {"role": "system", "content": "Follow the task contract."},
            {"role": "user", "content": "Audit all inputs and preserve the required final constraint."},
        ]
        for index in range(9):
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": f"Reading resource {index}.",
                        "tool_calls": [
                            {
                                "id": f"read-{index}",
                                "name": "read_file",
                                "arguments": {"path": f"/task/workspace/input-{index}.txt"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": f"read-{index}",
                        "content": "\n".join(
                            [
                                f"TITLE: input {index}",
                                "noise " + "x" * 1200,
                                (
                                    "REQUIRED FINAL CONSTRAINT: use normalized score"
                                    if index == 8
                                    else f"ordinary evidence {index}"
                                ),
                            ]
                        ),
                    },
                ]
            )
        manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=900, recent_history_fraction=0.05),
        )

        prepared = manager.prepare(
            tuple(messages),
            environment_state={},
            task_state={},
        )

        rendered = str(prepared.messages)
        selected = prepared.audit["selected_item_ids"]
        self.assertLessEqual(prepared.audit["estimated_input_tokens"], 900)
        self.assertTrue(prepared.audit["visible_evidence_workspace"]["block_selected"])
        self.assertIn("evidence:visible-workspace:dashboard", selected)
        self.assertTrue(
            any(item_id.startswith("evidence:visible-workspace:excerpt") for item_id in selected)
        )
        self.assertIn("visible_evidence_workspace", rendered)
        self.assertIn("visible_evidence_excerpt", rendered)
        self.assertIn("REQUIRED FINAL CONSTRAINT", rendered)

    def test_visible_workspace_excerpt_recency_stays_bounded_with_duplicates(self) -> None:
        messages = (
            {"role": "user", "content": "Inspect two resources."},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "read-1", "name": "read_file", "arguments": {"path": "/task/a.txt"}}
                ],
            },
            {"role": "tool", "tool_call_id": "read-1", "content": "TITLE: duplicate"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "read-2", "name": "read_file", "arguments": {"path": "/task/a.txt"}}
                ],
            },
            {"role": "tool", "tool_call_id": "read-2", "content": "TITLE: duplicate"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "read-3", "name": "read_file", "arguments": {"path": "/task/b.txt"}}
                ],
            },
            {"role": "tool", "tool_call_id": "read-3", "content": "TITLE: newest"},
        )
        manager = TaskAwareContextManager()
        workspace = EvidenceWorkspace.from_messages(messages)

        items, _ = manager._items({}, {}, messages, workspace)

        recencies = [
            item.recency
            for item in items
            if item.item_id.startswith("evidence:visible-workspace:excerpt")
        ]
        self.assertTrue(recencies)
        self.assertTrue(all(0.0 <= recency <= 1.0 for recency in recencies))


if __name__ == "__main__":
    unittest.main()
