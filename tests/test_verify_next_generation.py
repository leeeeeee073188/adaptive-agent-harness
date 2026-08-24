from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.verify_next_generation import _repository_public_artifact_scan, run_gate


class VerifyNextGenerationGateTests(unittest.TestCase):
    def test_gate_passes_zero_model_checks_and_rejects_bad_historical_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir, run_dir = _write_fixture(root, artifact_payload={"summary": "ok", "items": [{"id": "x"}]})
            output = root / "gate.json"

            with mock.patch(
                "scripts.verify_next_generation._run_test_modules",
                return_value={
                    "passed": True,
                    "returncode": 0,
                    "test_count": 42,
                    "command": ["python", "-m", "unittest"],
                },
            ):
                report = run_gate(
                    real_repo=root / "realrepo",
                    task_case=case_dir,
                    historical_run=run_dir,
                    output=output,
                    core_root=root / "core",
                )

            self.assertTrue(report["passed"], report)
            self.assertTrue(report["candidate_single_development_canary_allowed"])
            self.assertFalse(report["paid_expansion_allowed"])
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["new_tokens"], 0)
            self.assertIsNotNone(report["adaptive_source_sha256"])
            self.assertTrue(report["executable_policy_profile_fingerprint"])
            self.assertGreater(report["runtime_conformance"]["test_count"], 0)
            self.assertTrue(report["context_replay"]["history_dropped"])
            self.assertTrue(report["context_replay"]["visible_workspace_selected"])
            self.assertGreater(report["context_replay"]["task_excerpt_overlap_count"], 0)
            self.assertEqual(report["context_replay"]["secret_findings"], 0)
            self.assertTrue(report["contract"]["artifact_shape_criterion_present"])
            self.assertTrue(report["contract"]["source_access_criteria_present"])
            self.assertTrue(report["contract"]["artifact_non_vacuity_constraint_present"])
            self.assertTrue(report["contract"]["artifact_grounding_criterion_present"])
            self.assertGreater(report["contract"]["non_vacuous_collection_path_count"], 0)
            self.assertTrue(report["runtime_semantics"]["runtime_limit_response_rejected"])
            self.assertTrue(report["runtime_semantics"]["direct_public_script_is_transform"])
            self.assertTrue(report["historical_artifact"]["rejected"])
            self.assertTrue(report["historical_artifact"]["grounding_rejected"])
            self.assertGreater(report["historical_artifact"]["diagnostic_count"], 0)
            self.assertEqual(
                report["historical_artifact"]["diagnostic_types"],
                ["exact_provisional_copy", "missing_required_key"],
            )
            self.assertTrue(output.is_file())
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn('"summary": "ok"', rendered)
            self.assertNotIn('"id": "x"', rendered)

    def test_gate_fails_closed_when_required_paths_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "gate.json"
            report = run_gate(
                real_repo=root / "missing-realrepo",
                task_case=root / "missing-case",
                historical_run=root / "missing-run",
                output=output,
                core_root=root / "missing-core",
            )

            self.assertFalse(report["passed"])
            self.assertFalse(report["candidate_single_development_canary_allowed"])
            self.assertFalse(report["paid_expansion_allowed"])
            self.assertTrue(report["fail_closed"])
            self.assertTrue(any("missing path" in reason for reason in report["blockers"]))
            self.assertTrue(output.is_file())

    def test_gate_rejects_private_or_reward_path_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir, run_dir = _write_fixture(root, artifact_payload={"summary": "ok", "items": []})
            private_run = run_dir / "verifier"
            output = root / "gate.json"

            report = run_gate(
                real_repo=root / "realrepo",
                task_case=case_dir,
                historical_run=private_run,
                output=output,
                core_root=root / "core",
            )

            self.assertFalse(report["passed"])
            self.assertTrue(any("forbidden path component" in reason for reason in report["blockers"]))

    def test_gate_rejects_case_and_run_outside_real_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir, run_dir = _write_fixture(root, artifact_payload={"summary": "ok", "items": []})
            unrelated_repo = root / "unrelated-realrepo"
            unrelated_repo.mkdir()

            report = run_gate(
                real_repo=unrelated_repo,
                task_case=case_dir,
                historical_run=run_dir,
                output=root / "gate.json",
                core_root=root / "core",
            )

        self.assertFalse(report["passed"])
        self.assertIn("task_case must resolve inside real_repo", report["blockers"])
        self.assertIn("historical_run must resolve inside real_repo", report["blockers"])

    def test_public_artifact_scan_allows_public_task_ids_but_rejects_private_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            note = root / "docs/report.md"
            note.write_text("Public analysis for cli-example-task.", encoding="utf-8")

            allowed = _repository_public_artifact_scan(root)
            note.write_text("Do not publish private/expected_answer.json", encoding="utf-8")
            rejected = _repository_public_artifact_scan(root)

        self.assertTrue(allowed["passed"])
        self.assertEqual(allowed["public_task_reference_count"], 1)
        self.assertFalse(rejected["passed"])
        self.assertEqual(rejected["findings"][0]["type"], "evaluator_private_path")


def _write_fixture(root: Path, *, artifact_payload: object) -> tuple[Path, Path]:
    real_repo = root / "realrepo"
    real_repo.mkdir()
    core = root / "core"
    core.mkdir()
    (core / "kernel.py").write_text("POLICY = 'generic adaptive harness'\n", encoding="utf-8")

    case_dir = real_repo / "public-case"
    (case_dir / "workspace").mkdir(parents=True)
    (case_dir / "workspace" / "README.md").write_text("audit workspace with dataset notes\n", encoding="utf-8")
    task_prompt = """Open https://public.example.test/api/help and write outputs/quality_audit.json.
The `workspace/analysis/results.json` intermediate is a starting point, not truth.
Verify it against the raw source and report all matching records.

```json
{
  "summary": "short text",
  "items": [
    {"id": "dataset_alpha", "score": 1, "passed": true}
  ]
}
```
"""
    (case_dir / "task.md").write_text(task_prompt, encoding="utf-8")

    run_dir = real_repo / "runs/historical-run"
    agent_dir = run_dir / "agent"
    output_dir = run_dir / "workspace" / "outputs"
    agent_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    intermediate = run_dir / "workspace" / "workspace" / "analysis" / "results.json"
    intermediate.parent.mkdir(parents=True)
    intermediate.write_text(json.dumps(artifact_payload), encoding="utf-8")
    (output_dir / "quality_audit.json").write_text(json.dumps(artifact_payload), encoding="utf-8")
    messages = [
        {"type": "human", "content": task_prompt},
        {
            "type": "ai",
            "content": "I will inspect the workspace.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "bash",
                    "args": {"cmd": "cat workspace/README.md"},
                }
            ],
        },
        {
            "type": "tool",
            "tool_call_id": "call-1",
            "content": "workspace audit notes mention dataset_alpha quality audit schema",
        },
    ]
    for index in range(80):
        messages.append({"type": "ai", "content": f"old trajectory chunk {index} " + "noise " * 100})
    events = {"events": [{"type": "values", "data": {"messages": messages}}]}
    (agent_dir / "deerflow-events.json").write_text(json.dumps(events), encoding="utf-8")
    (agent_dir / "adaptive-ledger.jsonl").write_text(
        json.dumps({"type": "task/contract-created", "payload": {"contract": {"task_id": "fixture"}}}) + "\n",
        encoding="utf-8",
    )
    return case_dir, run_dir


if __name__ == "__main__":
    unittest.main()
