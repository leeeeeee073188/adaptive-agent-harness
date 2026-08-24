from __future__ import annotations

import unittest

from adaptive_harness.action_ledger import (
    ToolActionLedger,
    ToolIntent,
    VerificationDisposition,
    classify_tool_action,
)
from adaptive_harness.capabilities import ToolCall, ToolResult


class ToolActionLedgerTests(unittest.TestCase):
    def test_intent_classification_distinguishes_write_and_verification(self) -> None:
        write = classify_tool_action(
            "bash",
            {
                "description": "Build output",
                "command": "python3 -c \"import csv; f=open('/task/outputs/x.csv','w'); csv.writer(f)\"",
            },
        )
        verify = classify_tool_action(
            "bash",
            {
                "description": "Verify output row count",
                "command": "wc -l /task/outputs/x.csv && head -5 /task/outputs/x.csv",
            },
        )
        read = classify_tool_action(
            "read_file",
            {"path": "/task/workspace/input.json", "description": "Read input"},
        )

        self.assertEqual(write.intent, ToolIntent.WRITE)
        self.assertEqual(verify.intent, ToolIntent.VERIFY)
        self.assertEqual(read.intent, ToolIntent.READ)
        self.assertIn("outputs/x.csv", verify.resources)

        inspect = classify_tool_action(
            "bash",
            {
                "description": "Inspect JSON structure",
                "command": "python -c \"import json; print('key ->', json.dumps(data))\"",
            },
        )
        self.assertEqual(inspect.intent, ToolIntent.VERIFY)

    def test_description_does_not_change_execution_fingerprint(self) -> None:
        first = classify_tool_action(
            "read_file",
            {"path": "/task/workspace/input.json", "description": "First wording"},
        )
        second = classify_tool_action(
            "read_file",
            {"path": "/task/workspace/input.json", "description": "Different wording"},
        )

        self.assertEqual(first.argument_fingerprint, second.argument_fingerprint)
        self.assertEqual(first.scope_key, second.scope_key)

    def test_verification_budget_warns_then_resets_after_write(self) -> None:
        ledger = ToolActionLedger()

        def verify(number):
            return ledger.observe(
                ToolCall(
                    f"v{number}",
                    "bash",
                    {
                        "description": "Verify partial data",
                        "command": "python3 -c \"print(row['partial_data'])\"",
                    },
                ),
                ToolResult(f"v{number}", "same"),
            )[1]

        self.assertEqual(verify(1).disposition, VerificationDisposition.ALLOW)
        self.assertEqual(verify(2).disposition, VerificationDisposition.ALLOW)
        self.assertEqual(verify(3).disposition, VerificationDisposition.WARN)
        ledger.observe(
            ToolCall("w1", "write_file", {"path": "/task/outputs/result.csv"}),
            ToolResult("w1", "written"),
        )
        after_write = verify(4)

        self.assertEqual(after_write.disposition, VerificationDisposition.ALLOW)
        self.assertEqual(after_write.total_since_mutation, 1)

    def test_message_projection_ignores_orphan_results_and_clusters_scope(self) -> None:
        messages = (
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "bash",
                        "arguments": {
                            "description": "Check partial data",
                            "command": "python -c \"print(x['partial_data'])\"",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "40"},
            {"role": "tool", "tool_call_id": "orphan", "content": "ignored"},
        )

        ledger = ToolActionLedger.from_messages(messages)

        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0].semantics.intent, ToolIntent.VERIFY)
        self.assertEqual(ledger.records[0].semantics.fields, ("partial_data",))
        self.assertEqual(ledger.clusters()[0].attempts, 1)

    def test_total_verification_budget_catches_different_checks_before_mutation(self) -> None:
        ledger = ToolActionLedger()
        last = None
        for number, field in enumerate(("header", "rows", "sorting", "partial_data", "range"), 1):
            _, last = ledger.observe(
                ToolCall(
                    f"check-{number}",
                    "bash",
                    {
                        "description": f"Verify {field}",
                        "command": f"python -c \"print(row['{field}'])\"",
                    },
                ),
                ToolResult(f"check-{number}", "ok"),
            )

        self.assertIsNotNone(last)
        self.assertEqual(last.disposition, VerificationDisposition.WARN)
        self.assertEqual(last.total_since_mutation, 5)


if __name__ == "__main__":
    unittest.main()
