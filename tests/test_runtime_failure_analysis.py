from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_runtime_failure_modes import analyze_failure_modes


class RuntimeFailureAnalysisTests(unittest.TestCase):
    def test_reports_context_starvation_cross_tool_repeats_and_copied_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            task = run / "task"
            (task / "agent").mkdir(parents=True)
            (task / "workspace/outputs").mkdir(parents=True)
            (task / "workspace/source").mkdir()
            payload = '{"rows": []}\n'
            (task / "workspace/source/draft.json").write_text(payload, encoding="utf-8")
            (task / "workspace/outputs/result.json").write_text(payload, encoding="utf-8")
            (run / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "experiment": {"variant": "candidate"},
                        "results": [
                            {
                                "task_id": "task-1",
                                "run_dir": str(task),
                                "passed": False,
                                "capacity_score": 0.4,
                                "tool_call_count": 3,
                                "usage": {"total_tokens": 100},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            events = [
                {
                    "type": "task/contract-created",
                    "payload": {
                        "contract": {
                            "criteria": [{"kind": "artifact_exists", "required": True}]
                        }
                    },
                },
                {
                    "type": "tool/action-audited",
                    "payload": {
                        "record": {
                            "intent": "read",
                            "tool_name": "read_file",
                            "resources": ["source/draft.json"],
                        }
                    },
                },
                {
                    "type": "tool/action-audited",
                    "payload": {
                        "record": {
                            "intent": "read",
                            "tool_name": "bash",
                            "resources": ["source/draft.json"],
                        }
                    },
                },
                {
                    "type": "failure/classified",
                    "payload": {"failure": {"error_type": "NO_PROGRESS"}},
                },
                {
                    "type": "context/selected",
                    "payload": {
                        "adapter": "deerflow-model-middleware-v1",
                        "history_dropped": 7,
                        "layer_tokens": {"failure": 0},
                    },
                },
                {
                    "type": "runtime/error",
                    "payload": {
                        "type": "ValueError",
                        "message": "mutation epoch cannot move backwards",
                    },
                },
            ]
            (task / "agent/adaptive-ledger.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            report = analyze_failure_modes([run], "task-1")

        row = report["runs"][0]
        self.assertTrue(row["signals"]["artifact_only_contract"])
        self.assertTrue(row["signals"]["durable_failure_state_missing_from_model_context"])
        self.assertEqual(row["signals"]["repeated_resource_accesses"], 1)
        self.assertEqual(row["signals"]["cross_tool_repeated_resource_count"], 1)
        self.assertTrue(row["signals"]["mutation_epoch_regression"])
        self.assertEqual(
            row["artifact_matches_non_output_files"],
            [
                {
                    "output": "outputs/result.json",
                    "matching_source": "source/draft.json",
                }
            ],
        )
        self.assertEqual(report["analysis_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
