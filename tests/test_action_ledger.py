from __future__ import annotations

import json
import unittest
from pathlib import Path

from adaptive_harness.action_ledger import (
    AdviceControlEvidence,
    AdviceEligibilityGate,
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

    def test_deterministic_browser_success_controls_have_no_warnings(self) -> None:
        fixture = Path(__file__).parent / "fixtures/browser_guard_success_controls.json"
        controls = json.loads(fixture.read_text())["controls"]
        for control in controls:
            with self.subTest(control=control["id"]):
                ledger = ToolActionLedger()
                for index, raw in enumerate(control["tool_calls"], 1):
                    call = ToolCall(
                        f"{control['id']}:{index}",
                        raw["name"],
                        raw.get("args") or {},
                    )
                    ledger.observe(call, ToolResult(call.id, f"result-{index}"))
                self.assertNotIn(ToolIntent.UNKNOWN, {r.semantics.intent for r in ledger.records})
                self.assertTrue(
                    all(
                        decision.disposition is VerificationDisposition.ALLOW
                        for decision in ledger.decisions
                    )
                )

    def test_repeated_observation_warns_until_browser_interaction_resets(self) -> None:
        ledger = ToolActionLedger()
        for index in range(1, 6):
            _, decision = ledger.observe(
                ToolCall(f"view-{index}", "view_image", {"path": "/task/frame.png"}),
                ToolResult(f"view-{index}", "same-frame"),
            )
        self.assertEqual(decision.disposition, VerificationDisposition.WARN)

        ledger.observe(
            ToolCall("click", "browser_click", {"ref": 4}),
            ToolResult("click", "clicked"),
        )
        _, after_click = ledger.observe(
            ToolCall("view-new", "view_image", {"path": "/task/frame-2.png"}),
            ToolResult("view-new", "new-frame"),
        )
        self.assertEqual(after_click.disposition, VerificationDisposition.ALLOW)

    def test_advice_gate_uses_classification_and_wilson_control_bounds(self) -> None:
        eligible = AdviceEligibilityGate().evaluate(
            AdviceControlEvidence(
                classified_actions=200,
                total_actions=200,
                success_controls=7,
                success_controls_warned=0,
                failure_controls=2,
                failure_controls_signaled=2,
                mutation_blocks=0,
            )
        )
        underpowered = AdviceEligibilityGate().evaluate(
            AdviceControlEvidence(
                classified_actions=80,
                total_actions=100,
                success_controls=2,
                success_controls_warned=0,
                failure_controls=1,
                failure_controls_signaled=1,
                mutation_blocks=0,
            )
        )

        self.assertTrue(eligible.eligible)
        self.assertLess(eligible.false_warning_wilson_upper, 0.40)
        self.assertGreater(eligible.failure_signal_wilson_lower, 0.30)
        self.assertFalse(underpowered.eligible)
        self.assertGreaterEqual(len(underpowered.reasons), 2)


if __name__ == "__main__":
    unittest.main()
