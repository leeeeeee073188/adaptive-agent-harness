from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_minibench_pair import analyze_pair


class CanaryAnalysisTests(unittest.TestCase):
    def test_semantic_newline_equivalence_does_not_hide_cost_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate = _pair_fixture(root, baseline_tokens=100, candidate_tokens=160)

            report = analyze_pair(
                baseline,
                candidate,
                "task-1",
                max_token_increase=0.25,
            )

        self.assertTrue(report["pair_valid"])
        self.assertTrue(report["gates"]["semantic_outputs_equal"])
        self.assertFalse(report["gates"]["token_cost_within_limit"])
        self.assertFalse(report["continue_block"])

    def test_valid_low_cost_pair_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate = _pair_fixture(root, baseline_tokens=100, candidate_tokens=110)

            report = analyze_pair(
                baseline,
                candidate,
                "task-1",
                max_token_increase=0.25,
            )

        self.assertTrue(report["continue_block"])


def _pair_fixture(root: Path, *, baseline_tokens: int, candidate_tokens: int) -> tuple[Path, Path]:
    task_dirs = {}
    for variant in ("baseline", "candidate"):
        run = root / variant
        task = run / "tasks/task-1"
        output = task / "workspace/outputs"
        agent = task / "agent"
        output.mkdir(parents=True)
        agent.mkdir()
        (output / "result.csv").write_bytes(
            b"a,b\r\n1,2\r\n" if variant == "baseline" else b"a,b\n1,2\n"
        )
        (task / "integrity.json").write_text(json.dumps({"passed": True}))
        task_dirs[variant] = task

    ledger = [
        {"type": "evidence/added", "payload": {"evidence": {"id": "e1"}}},
        {"type": "completion/checked", "payload": {"passed": True}},
    ]
    (task_dirs["candidate"] / "agent/adaptive-ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in ledger)
    )
    for variant, tokens in (("baseline", baseline_tokens), ("candidate", candidate_tokens)):
        run = root / variant
        _write_json(
            run / "summary.json",
            {
                "run_id": variant,
                "model_name": "model",
                "image": "image",
                "experiment": {"variant": variant, "seed": 7, "split": "dev"},
                "results": [
                    {
                        "task_id": "task-1",
                        "run_dir": str(task_dirs[variant]),
                        "passed": True,
                        "capacity_score": 1.0,
                        "integrity_passed": True,
                        "usage": {"total_tokens": tokens},
                        "tool_call_count": 2,
                        "elapsed_sec": 1.0,
                    }
                ],
            },
        )
    return root / "baseline", root / "candidate"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
