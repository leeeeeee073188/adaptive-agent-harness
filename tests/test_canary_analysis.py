from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_minibench_pair import analyze_pair


class CanaryAnalysisTests(unittest.TestCase):
    def test_high_token_growth_is_reported_without_blocking_a_valid_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate = _pair_fixture(root, baseline_tokens=100, candidate_tokens=160)

            report = analyze_pair(
                baseline,
                candidate,
                "task-1",
                token_diagnostic_threshold=0.25,
            )

        self.assertTrue(report["pair_valid"])
        self.assertTrue(report["gates"]["semantic_outputs_equal"])
        self.assertTrue(report["cost"]["token_increase_over_diagnostic_threshold"])
        self.assertFalse(report["cost"]["architecture_increase_over_diagnostic_threshold"])
        self.assertEqual(report["cost"]["attributable_architecture_token_delta"], 0)
        self.assertEqual(report["cost"]["attribution"], "provider_or_trajectory_variance")
        self.assertTrue(report["continue_block"])
        self.assertEqual(report["decision"], "continue Block 1")

    def test_valid_low_cost_pair_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate = _pair_fixture(root, baseline_tokens=100, candidate_tokens=110)

            report = analyze_pair(
                baseline,
                candidate,
                "task-1",
                token_diagnostic_threshold=0.25,
            )

        self.assertTrue(report["continue_block"])

    def test_action_repeated_after_block_scope_stops_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate = _pair_fixture(
                root,
                baseline_tokens=100,
                candidate_tokens=120,
                resource_events=(
                    {
                        "type": "resource/no-progress-checked",
                        "payload": {
                            "disposition": "block_scope",
                            "scope_key": "scope-a",
                            "strategy_fingerprint": "strategy-a",
                        },
                    },
                    {
                        "type": "tool/action-audited",
                        "payload": {
                            "record": {
                                "scope_key": "scope-a",
                                "argument_fingerprint": "strategy-a",
                            }
                        },
                    },
                ),
            )

            report = analyze_pair(
                baseline,
                candidate,
                "task-1",
                token_diagnostic_threshold=0.25,
            )

        self.assertFalse(report["continue_block"])
        self.assertTrue(
            report["resource_guardrail"]["uncontrolled_no_progress_loop"]
        )


def _pair_fixture(
    root: Path,
    *,
    baseline_tokens: int,
    candidate_tokens: int,
    resource_events: tuple[dict[str, object], ...] = (),
) -> tuple[Path, Path]:
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
        (task / "integrity.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "prompt_sha256": "prompt-hash",
                    "config_sha256": "config-hash",
                }
            )
        )
        task_dirs[variant] = task

    ledger = [
        {"type": "evidence/added", "payload": {"evidence": {"id": "e1"}}},
        {"type": "completion/checked", "payload": {"passed": True}},
        *resource_events,
    ]
    (task_dirs["candidate"] / "agent/adaptive-ledger.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in ledger)
    )
    _write_json(
        task_dirs["candidate"] / "agent/deerflow-events.json",
        {"events": [{"type": "end", "data": {"usage": {"total_tokens": candidate_tokens}}}]},
    )
    _write_json(
        task_dirs["candidate"] / "agent/deerflow-trajectory.json",
        {
            "response_text": "",
            "tool_calls": [],
            "tool_results": [],
            "usage": {"total_tokens": candidate_tokens},
            "adaptive": {"turns": 1},
        },
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
