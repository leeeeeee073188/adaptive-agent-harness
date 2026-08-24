from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_context_working_set import analyze_runs


class ContextAnalysisTests(unittest.TestCase):
    def test_historical_counterfactual_deduplicates_snapshots_and_preserves_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "task-run"
            agent_dir = run_dir / "agent"
            agent_dir.mkdir(parents=True)
            task = {"type": "human", "content": "Complete the public task.", "id": "u1"}
            assistant = {
                "type": "ai",
                "content": "Inspecting.",
                "id": "a1",
                "tool_calls": [{"id": "c1", "name": "inspect", "args": {}}],
            }
            tool = {
                "type": "tool",
                "content": "x" * 800,
                "id": "t1",
                "tool_call_id": "c1",
            }
            final = {"type": "ai", "content": "Done.", "id": "a2"}
            events = (
                {"type": "values", "data": {"messages": [task]}},
                {"type": "values", "data": {"messages": [task, assistant]}},
                {"type": "values", "data": {"messages": [task, assistant, tool]}},
                {"type": "values", "data": {"messages": [task, assistant, tool]}},
                {"type": "values", "data": {"messages": [task, assistant, tool, final]}},
            )
            (agent_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            report = analyze_runs((run_dir,), budget_tokens=128)

        self.assertTrue(report["all_invariants_passed"])
        self.assertEqual(report["snapshot_count"], 3)
        self.assertIsNotNone(report["median_run_surface_reduction_fraction"])
        self.assertLessEqual(
            report["selected_message_surface_tokens"],
            report["full_message_surface_tokens"],
        )
        self.assertEqual(report["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
