from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_diagnostic_matrix import analyze_run, build_report


class DiagnosticMatrixAnalysisTests(unittest.TestCase):
    def test_public_run_projects_cross_layer_failure_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "diagnostic-api"
            task = run / "tasks/01-api-example"
            agent = task / "agent"
            outputs = task / "workspace/outputs"
            agent.mkdir(parents=True)
            (outputs / "audit").mkdir(parents=True)
            (outputs / "audit/trace.json").write_text("{}", encoding="utf-8")
            (outputs / "scratch.json").write_text("{}", encoding="utf-8")
            summary = {
                "run_id": "diagnostic-api",
                "total": 1,
                "results": [
                    {
                        "task_id": "api-example",
                        "run_dir": str(task),
                        "passed": False,
                        "capacity_score": 0.1,
                        "checks_passed": 1,
                        "checks_total": 10,
                        "elapsed_sec": 12.0,
                        "usage": {
                            "input_tokens": 90,
                            "output_tokens": 10,
                            "total_tokens": 100,
                        },
                        "tool_call_count": 3,
                        "agent_exec_returncode": 1,
                        "integrity_passed": True,
                        "llm_judge_score": None,
                        "modality": "text_only",
                    }
                ],
            }
            (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            events = [
                {
                    "type": "task/contract-created",
                    "payload": {
                        "contract": {
                            "criteria": [
                                {
                                    "kind": "artifact_exists",
                                    "parameters": {"path": "outputs/answer.json"},
                                },
                                {
                                    "kind": "artifact_exists",
                                    "parameters": {"path": "outputs/audit/"},
                                },
                            ]
                        }
                    },
                },
                {
                    "type": "tool/action-audited",
                    "payload": {
                        "record": {"intent": "read", "error_type": "TOOL_ERROR"},
                        "decision": {"disposition": "record"},
                    },
                },
                {
                    "type": "failure/classified",
                    "payload": {"failure": {"error_type": "NO_PROGRESS"}},
                },
                {
                    "type": "completion/checked",
                    "payload": {
                        "missing": ["Artifact does not exist: outputs/answer.json"],
                        "assessments": [
                            {
                                "criterion_id": "artifact:outputs-answer-json",
                                "status": "unsatisfied",
                            },
                            {
                                "criterion_id": "artifact:outputs-audit",
                                "status": "unsatisfied",
                            },
                        ],
                    },
                },
                {
                    "type": "runtime/error",
                    "payload": {"type": "HTTPError", "message": "403"},
                },
            ]
            (agent / "adaptive-ledger.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            row = analyze_run(run)
            report = build_report([run])

        self.assertEqual(row["missing_required_artifacts"], ["outputs/answer.json"])
        self.assertEqual(row["matched_required_artifacts"], ["outputs/audit/"])
        self.assertEqual(row["unrelated_output_paths"], ["outputs/scratch.json"])
        self.assertEqual(
            row["required_directory_exists_but_unsatisfied"],
            ["outputs/audit/"],
        )
        self.assertEqual(report["cross_type_signals"]["tool_error_count"], 1)
        self.assertEqual(report["cross_type_signals"]["no_progress_failure_count"], 1)
        self.assertEqual(report["cross_type_signals"]["runtime_error_runs"], ["diagnostic-api"])
        self.assertEqual(report["analysis_model_calls"], 0)
        self.assertFalse(report["paid_expansion_allowed"])

    def test_evaluator_private_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "verifier/run"
            private.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "evaluator-private"):
                analyze_run(private)

    def test_output_symlink_to_private_data_is_not_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "diagnostic-file"
            task = run / "tasks/01-file-example"
            agent = task / "agent"
            outputs = task / "workspace/outputs"
            private = task / "private"
            agent.mkdir(parents=True)
            outputs.mkdir(parents=True)
            private.mkdir(parents=True)
            secret = private / "secret.txt"
            secret.write_text("never hash me", encoding="utf-8")
            (outputs / "leak.txt").symlink_to(secret)
            summary = {
                "run_id": "diagnostic-file",
                "total": 1,
                "results": [
                    {
                        "task_id": "file-example",
                        "run_dir": str(task),
                        "passed": False,
                        "capacity_score": 0.0,
                        "checks_passed": 0,
                        "checks_total": 1,
                        "elapsed_sec": 1.0,
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        "tool_call_count": 0,
                        "agent_exec_returncode": 0,
                        "integrity_passed": True,
                        "llm_judge_score": None,
                        "modality": "text_only",
                    }
                ],
            }
            (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            (agent / "adaptive-ledger.jsonl").write_text(
                json.dumps(
                    {
                        "type": "task/contract-created",
                        "payload": {"contract": {"criteria": []}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            row = analyze_run(run)

        self.assertEqual(row["output_inventory"], [])


if __name__ == "__main__":
    unittest.main()
