from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_tool_action_ledger import analyze_runs


class ToolActionLedgerAnalysisTests(unittest.TestCase):
    def test_counterfactual_warns_without_blocking_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            agent = run / "agent"
            agent.mkdir(parents=True)
            messages = [{"type": "human", "content": "task", "id": "u"}]
            events = []
            for index in range(1, 6):
                messages.extend(
                    (
                        {
                            "type": "ai",
                            "content": "",
                            "id": f"a{index}",
                            "tool_calls": [
                                {
                                    "id": f"c{index}",
                                    "name": "bash",
                                    "args": {
                                        "description": f"Verify field {index}",
                                        "command": f"python -c \"print(row['field_{index}'])\"",
                                    },
                                }
                            ],
                        },
                        {
                            "type": "tool",
                            "content": "ok",
                            "id": f"t{index}",
                            "tool_call_id": f"c{index}",
                        },
                    )
                )
            messages.extend(
                (
                    {
                        "type": "ai",
                        "content": "",
                        "id": "aw",
                        "tool_calls": [
                            {
                                "id": "cw",
                                "name": "write_file",
                                "args": {"path": "/task/outputs/result.csv"},
                            }
                        ],
                    },
                    {"type": "tool", "content": "written", "id": "tw", "tool_call_id": "cw"},
                )
            )
            events.append({"type": "values", "data": {"messages": messages}})
            (agent / "events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            report = analyze_runs([run])

        self.assertTrue(report["all_invariants_passed"])
        self.assertEqual(report["verification_warnings"], 1)
        self.assertEqual(report["intent_counts"]["write"], 1)
        self.assertEqual(report["runs"][0]["mutation_blocks"], 0)


if __name__ == "__main__":
    unittest.main()

