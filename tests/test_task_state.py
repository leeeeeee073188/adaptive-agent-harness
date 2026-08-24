from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.capabilities import ToolResult
from adaptive_harness.integrations.realreplica_contract import realreplica_contract_builder
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.recovery import (
    RecoveryOutcome,
    RuleBasedTaskRecoveryExecutor,
    TaskFailureCategory,
    TaskRecoveryAction,
    TaskRecoveryDecision,
)
from adaptive_harness.task_contract import (
    CriterionKind,
    CriterionSource,
    RuleBasedTaskContractBuilder,
)
from adaptive_harness.task_state import (
    COMPLETION_CHECKED,
    CriterionStatus,
    Evidence,
    EvidenceKind,
    EvidenceSource,
    Failure,
    RecoveryExecutionRecord,
    RecoveryRecord,
    TaskEventWriter,
    TaskStateProjector,
    evidence_from_tool_result,
)


class TaskContractStateTests(unittest.TestCase):
    def test_missing_artifact_and_exact_count_require_runtime_evidence(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "task-1",
            "Write outputs/report.csv containing exactly 3 records.",
        )
        ledger = SessionLedger("run-1")
        writer = TaskEventWriter(ledger)
        writer.create_contract(contract)

        missing = writer.check_completion()
        writer.add_evidence(
            Evidence(
                "artifact-1",
                EvidenceKind.ARTIFACT,
                "/task/outputs/report.csv",
                {"exists": True, "sha256": "abc"},
                EvidenceSource.ARTIFACT_INSPECTION,
            )
        )
        writer.add_evidence(
            Evidence(
                "count-1",
                EvidenceKind.COUNT,
                "records",
                2,
                EvidenceSource.RUNTIME_OBSERVATION,
            )
        )
        wrong_count = writer.check_completion()
        writer.add_evidence(
            Evidence(
                "count-2",
                EvidenceKind.COUNT,
                "records",
                3,
                EvidenceSource.RUNTIME_OBSERVATION,
            )
        )
        complete = writer.check_completion()

        self.assertFalse(missing.passed)
        self.assertIn("Missing artifact evidence", " ".join(missing.missing))
        self.assertFalse(wrong_count.passed)
        self.assertIn("observed 2", " ".join(wrong_count.missing))
        self.assertTrue(complete.passed)
        self.assertEqual([item.kind for item in contract.criteria], [
            CriterionKind.ARTIFACT_EXISTS,
            CriterionKind.EXACT_COUNT,
        ])
        self.assertTrue(all(item.source is CriterionSource.TASK_PROMPT for item in contract.criteria))
        self.assertEqual(sum(event.type == COMPLETION_CHECKED for event in ledger.events), 3)

    def test_dependency_stays_blocked_until_prerequisite_has_evidence(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "task-2",
            "Prepare and publish the report.",
            {
                "artifacts": [{"id": "prepare", "path": "outputs/report.csv"}],
                "criteria": [
                    {
                        "id": "publish",
                        "kind": "dependency",
                        "description": "Publish only after preparing the artifact",
                        "depends_on": ["prepare"],
                    }
                ],
            },
        )
        ledger = SessionLedger("run-2")
        writer = TaskEventWriter(ledger)
        writer.create_contract(contract)

        blocked = writer.check_completion()
        writer.add_evidence(
            Evidence(
                "artifact-2",
                EvidenceKind.ARTIFACT,
                "outputs/report.csv",
                True,
                EvidenceSource.ARTIFACT_INSPECTION,
            )
        )
        complete = writer.check_completion()

        statuses = {item.criterion_id: item.status for item in blocked.assessments}
        self.assertEqual(statuses["prepare"], CriterionStatus.PENDING)
        self.assertEqual(statuses["publish"], CriterionStatus.BLOCKED)
        self.assertTrue(complete.passed)

    def test_projection_is_deleted_and_rebuilt_from_jsonl(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "task-3",
            "Create the required output.",
            {"artifacts": ["outputs/result.json"]},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ledger = SessionLedger("run-3", path)
            writer = TaskEventWriter(ledger)
            writer.create_contract(contract)
            writer.update_state({"current_subgoal": "write-result", "attempts": 1}, reason="tool started")
            writer.add_evidence(
                Evidence(
                    "artifact-3",
                    EvidenceKind.ARTIFACT,
                    "outputs/result.json",
                    True,
                    EvidenceSource.TOOL_RESULT,
                )
            )
            writer.classify_failure(
                Failure(
                    "failure-1",
                    "TRANSIENT_IO",
                    "first write was interrupted",
                    EvidenceSource.TOOL_RESULT,
                    {"recovered": True},
                )
            )
            recovery = RecoveryRecord(
                    TaskFailureCategory.ARTIFACT_ERROR,
                    (TaskFailureCategory.PREMATURE_FINISH,),
                    TaskRecoveryDecision(
                        (
                            TaskRecoveryAction.VALIDATE_CONTRACT,
                            TaskRecoveryAction.WRITE_PARTIAL,
                        ),
                        True,
                        "bounded recovery",
                    ),
                )
            writer.record_recovery(recovery)
            execution_event = writer.record_recovery_execution(
                RecoveryExecutionRecord(
                    RuleBasedTaskRecoveryExecutor().execute(
                        recovery.decision,
                        missing=("artifact",),
                    )
                )
            )
            writer.record_recovery_outcome(
                RecoveryOutcome(
                    execution_event.seq,
                    recovery.decision.actions,
                    "progressed",
                    False,
                    True,
                    "progress followed recovery",
                )
            )
            writer.check_completion()
            before = TaskStateProjector().project(ledger.events)

            del writer, ledger
            replayed = SessionLedger.replay(path)
            after = TaskStateProjector().project(replayed.events)

        self.assertEqual(after, before)
        self.assertEqual(after.values["attempts"], 1)
        self.assertEqual(after.failures[0].error_type, "TRANSIENT_IO")
        self.assertEqual(
            after.recoveries[0].decision.actions,
            (
                TaskRecoveryAction.VALIDATE_CONTRACT,
                TaskRecoveryAction.WRITE_PARTIAL,
            ),
        )
        self.assertEqual(len(after.recovery_executions), 1)
        self.assertEqual(len(after.recovery_outcomes), 1)
        self.assertTrue(after.recovery_outcomes[0].effective)
        self.assertEqual(
            after.values["recovery.missing_requirements"],
            ["artifact"],
        )
        self.assertTrue(after.latest_completion and after.latest_completion.passed)

    def test_model_context_is_bounded_but_ledger_keeps_full_evidence(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "task-context",
            "Create outputs/result.txt.",
        )
        ledger = SessionLedger("run-context")
        writer = TaskEventWriter(ledger)
        writer.create_contract(contract)
        long_observation = "x" * 10_000
        writer.add_evidence(
            Evidence(
                "large-observation",
                EvidenceKind.OBSERVATION,
                "command output",
                long_observation,
                EvidenceSource.TOOL_RESULT,
            )
        )

        state = TaskStateProjector().project(ledger.events)
        context = state.to_context()

        self.assertEqual(state.evidence[0].value, long_observation)
        self.assertLess(len(context["evidence"][0]["value"]), 600)
        self.assertIn("chars omitted", context["evidence"][0]["value"])

    def test_public_schema_rejects_evaluation_only_fields(self) -> None:
        builder = RuleBasedTaskContractBuilder()

        with self.assertRaisesRegex(ValueError, "evaluation-only field"):
            builder.build(
                "task-4",
                "Create an output.",
                {"criteria": [], "metadata": {"ground_truth": "hidden answer"}},
            )

        empty_contract = builder.build("task-empty", "Summarize the request.")
        ledger = SessionLedger("run-empty")
        writer = TaskEventWriter(ledger)
        writer.create_contract(empty_contract)
        result = writer.check_completion()
        self.assertFalse(result.passed)
        self.assertEqual(result.missing, ("verifiable criteria",))

    def test_invalid_dependency_graph_fails_closed(self) -> None:
        builder = RuleBasedTaskContractBuilder()
        schema = {
            "criteria": [
                {"id": "a", "kind": "dependency", "depends_on": ["b"]},
                {"id": "b", "kind": "dependency", "depends_on": ["a"]},
            ]
        }

        with self.assertRaisesRegex(ValueError, "cycle"):
            builder.build("task-5", "Do the task.", schema)

    def test_invalid_tool_evidence_schema_fails_closed(self) -> None:
        result = ToolResult("call-1", "claimed success", metadata={"evidence": "not-structured"})

        with self.assertRaisesRegex(ValueError, "must be a list"):
            evidence_from_tool_result(result)

    def test_output_directory_declaration_extracts_quoted_files_and_word_count(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "task-output-list",
            "Write exactly three files to `outputs/`: `one.json`, `two.md`, and `three.csv`. ",
        )

        artifact_paths = {
            item.parameters["path"]
            for item in contract.criteria
            if item.kind is CriterionKind.ARTIFACT_EXISTS
        }
        count = next(item for item in contract.criteria if item.kind is CriterionKind.EXACT_COUNT)
        self.assertEqual(artifact_paths, {"outputs/one.json", "outputs/two.md", "outputs/three.csv"})
        self.assertEqual(count.parameters, {"subject": "files", "expected": 3})

        direct = RuleBasedTaskContractBuilder().build(
            "task-direct-output",
            "Read `input.json` and write `outputs/result.json`; keep `notes.md` as reference.",
        )
        self.assertEqual(
            [item.parameters["path"] for item in direct.criteria],
            ["outputs/result.json"],
        )

    def test_public_state_transition_language_generates_provider_neutral_criteria(self) -> None:
        builder = realreplica_contract_builder()
        listing = builder.build("listing", "帮我把商品发上线，发品系统打开后提交。")
        workspace = builder.build(
            "workspace",
            "创建顶层标签，保存一封未发送草稿，再创建一个日历事件。",
        )
        document = builder.build("docs", "Apply the update to the price list document now.")

        self.assertEqual(
            [item.parameters["subject"] for item in listing.criteria],
            ["listing.submitted"],
        )
        self.assertEqual(
            {item.parameters["subject"] for item in workspace.criteria},
            {"mail.label_created", "mail.draft_saved", "calendar.event_created"},
        )
        self.assertEqual(document.criteria[0].parameters["subject"], "document.updated")

        targeted = builder.build(
            "targets",
            "创建顶层标签 `VBR-52`，再建一个 `VBR-52 Harbor Stitch` 日历事件。 "
            'The document is titled **"AccessoryHub Wholesale Price List — Q3 2026"**; update it.',
        )
        targets = {
            item.parameters["subject"]: item.parameters.get("target")
            for item in targeted.criteria
        }
        self.assertEqual(targets["mail.label_created"], "VBR-52")
        self.assertEqual(targets["calendar.event_created"], "VBR-52 Harbor Stitch")
        self.assertEqual(
            targets["document.updated"],
            "AccessoryHub Wholesale Price List — Q3 2026",
        )

        contains_any = builder.build(
            "workbench",
            "不要删除已有的日历事件；在日历里创建会议事件；"
            "标题里要带 `Solar Pump` 或 `RFQ`。",
        )
        self.assertEqual(
            contains_any.criteria[0].parameters["target_any"],
            ["Solar Pump", "RFQ"],
        )

    def test_per_item_count_and_enum_phrases_are_not_misparsed_as_global_counts(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "per-item",
            "Produce exactly one entry per RFQ file. Use exactly one of: A, B, C.",
        )

        self.assertEqual(contract.criteria, ())


if __name__ == "__main__":
    unittest.main()
