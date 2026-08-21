from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from adaptive_harness.evaluation import RolloutRecord, summarize_paired_rollouts
from adaptive_harness.integrations.realreplica import (
    RealReplicaMiniBenchAdapter,
    VariantSpec,
    stable_profile_fingerprint,
)
from scripts.preflight_minibench16 import DEFAULT_IMAGE, _bridge_evidence_valid

IMAGE = "realreplicabench/deerflow:test"


class EvaluationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task_ids = _write_minibench_fixture(self.root)
        self.adapter = RealReplicaMiniBenchAdapter()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_loader_pins_frozen_subset_and_public_prompts(self) -> None:
        dataset = self.adapter.load(self.root)
        coverage = self.adapter.contract_coverage(dataset)
        provider_coverage = self.adapter.provider_coverage(dataset)

        self.assertEqual(len(dataset.tasks), 16)
        self.assertEqual(dataset.counts()["difficulty_band"], {"easy": 4, "hard": 8, "medium": 4})
        self.assertEqual(coverage.covered_count, 8)
        self.assertEqual(coverage.total_count, 16)
        self.assertEqual(provider_coverage.enforced_task_count, 8)
        self.assertEqual(provider_coverage.ready_blocks, ())
        self.assertEqual(len(dataset.fingerprint), 64)

    def test_selection_order_drift_fails_closed(self) -> None:
        path = self.root / "splits/minibench16.blocks.json"
        document = json.loads(path.read_text())
        document["blocks"][0]["task_ids"].reverse()
        path.write_text(json.dumps(document))

        with self.assertRaisesRegex(ValueError, "ordering must match"):
            self.adapter.load(self.root)

    def test_paired_manifest_controls_model_image_seed_and_profile(self) -> None:
        dataset = self.adapter.load(self.root)
        baseline = _variant("baseline", dataset.seed, "baseline-profile")
        candidate = _variant("candidate", dataset.seed, "candidate-profile")

        manifest = self.adapter.paired_manifest(dataset, baseline, candidate)

        self.assertEqual(len(manifest.cells), 32)
        self.assertEqual(manifest.cells[0].pair_key, manifest.cells[1].pair_key)
        self.assertEqual({cell.variant for cell in manifest.cells}, {"baseline", "candidate"})
        with self.assertRaisesRegex(ValueError, "same model"):
            self.adapter.paired_manifest(
                dataset,
                baseline,
                VariantSpec("other", "other-profile", "other-model", IMAGE, dataset.seed),
            )

    def test_historical_baseline_requires_integrity_and_covers_all_tasks(self) -> None:
        dataset = self.adapter.load(self.root)
        history = self.adapter.historical_baselines(
            self.root,
            dataset,
            _variant("baseline", dataset.seed, "baseline-profile"),
        )

        self.assertEqual(set(history), set(self.task_ids))
        self.assertEqual(sum(row.passed for row in history.values()), 8)
        self.assertEqual(sum(row.total_tokens or 0 for row in history.values()), 16_000)

    def test_paired_statistics_ignore_unmatched_rollouts(self) -> None:
        records = [
            RolloutRecord(
                "a", "r1", "", "t1",
                variant="base", passed=False, capacity_score=0.4, usage={"total_tokens": 100},
            ),
            RolloutRecord(
                "a", "r2", "", "t2",
                variant="cand", passed=True, capacity_score=0.8, usage={"total_tokens": 120},
            ),
            RolloutRecord(
                "unmatched", "r3", "", "t3",
                variant="base", passed=True, capacity_score=1.0, usage={"total_tokens": 999},
            ),
        ]

        stats = summarize_paired_rollouts(records, baseline_variant="base", candidate_variant="cand")

        self.assertEqual(stats.matched_samples, 1)
        self.assertEqual(stats.pass_rate_delta, 1.0)
        self.assertAlmostEqual(stats.capacity_score_delta, 0.4)
        self.assertEqual(stats.total_token_delta, 20)

    def test_profile_fingerprint_rejects_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            stable_profile_fingerprint({"api_key": "secret"})

    def test_paid_bridge_gate_requires_pinned_container_evidence(self) -> None:
        path = self.root / "bridge-evidence.json"
        payload = {
            "passed": True,
            "runtime_image": DEFAULT_IMAGE,
            "dataset_fingerprint": "dataset-fp",
            "candidate_profile_fingerprint": "candidate-fp",
            "model_calls": 0,
            "realreplica_candidate_runner_wired": True,
            "checks": {
                "adaptive_package_imported": True,
                "embedded_client_stream_exercised": True,
                "ledger_persisted": True,
                "policy_bridge_enabled": True,
            },
        }
        _write_json(path, payload)

        self.assertTrue(
            _bridge_evidence_valid(
                path,
                dataset_fingerprint="dataset-fp",
                candidate_profile_fingerprint="candidate-fp",
            )
        )
        payload["checks"].pop("ledger_persisted")
        _write_json(path, payload)
        self.assertFalse(
            _bridge_evidence_valid(
                path,
                dataset_fingerprint="dataset-fp",
                candidate_profile_fingerprint="candidate-fp",
            )
        )


def _variant(name: str, seed: int, profile: str) -> VariantSpec:
    return VariantSpec(name, profile, "deepseek-v4-flash", IMAGE, seed)


def _write_minibench_fixture(root: Path) -> list[str]:
    split_dir = root / "splits"
    dataset_dir = root / "datasets_domain_v1"
    split_dir.mkdir(parents=True)
    categories = ["cli", "browser", "file", "api"]
    difficulties = ["easy"] * 4 + ["medium"] * 4 + ["hard"] * 8
    capabilities = ["text-only", "browser-text", "vision", "text-only"] * 4
    task_ids = [f"task-{index:02d}" for index in range(16)]
    tasks = []
    for index, task_id in enumerate(task_ids):
        category = categories[index % len(categories)]
        task_dir = dataset_dir / category / task_id
        task_dir.mkdir(parents=True)
        prompt = (
            f"Write outputs/{task_id}.json."
            if index % 2 == 0
            else "Complete the public state transition."
        )
        (task_dir / "task.md").write_text(prompt)
        (task_dir / "task.toml").write_text(
            "\n".join(
                (
                    "[task]",
                    f'id = "{task_id}"',
                    f'name = "{task_id}"',
                    'language = "en-US"',
                    'entrypoint = "task.md"',
                    'rubric = "rubric.json"',
                    "[verifier]",
                    'type = "script"',
                )
            )
        )
        tasks.append(
            {
                "task_id": task_id,
                "category": category,
                "difficulty_raw": difficulties[index],
                "difficulty_band": difficulties[index],
                "capability": capabilities[index],
                "max_actions": 60,
                "timeout_sec": 1800,
                "smoke_reuse": index < 5,
            }
        )
    blocks = [
        {"block": block + 1, "purpose": f"block {block + 1}", "task_ids": task_ids[block * 4 : block * 4 + 4]}
        for block in range(4)
    ]
    selection_counts = {
        "total": 16,
        "category": dict(sorted(Counter(row["category"] for row in tasks).items())),
        "difficulty_band": dict(sorted(Counter(row["difficulty_band"] for row in tasks).items())),
        "capability": dict(sorted(Counter(row["capability"] for row in tasks).items())),
        "smoke_reused": 5,
    }
    _write_json(
        split_dir / "minibench16.collection.json",
        {"collection_id": "fixture-minibench16", "task_ids": task_ids},
    )
    _write_json(
        split_dir / "minibench16.selection.json",
        {
            "seed": 7,
            "tasks": tasks,
            "counts": selection_counts,
            "task_ids_sha256": hashlib.sha256(("\n".join(task_ids) + "\n").encode()).hexdigest(),
        },
    )
    _write_json(split_dir / "minibench16.blocks.json", {"seed": 7, "blocks": blocks})
    _write_json(split_dir / "dev.collection.json", {"task_ids": task_ids})
    _write_json(split_dir / "heldout.collection.json", {"task_ids": []})

    run_dir = root / "runs/baseline"
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": "baseline",
            "started_at": "2026-01-01T00:00:00",
            "harness": "deerflow",
            "model_name": "deepseek-v4-flash",
            "image": IMAGE,
            "run_config_sha256": "config-hash",
            "experiment": {"variant": "baseline", "seed": 7},
            "results": [
                {
                    "task_id": task_id,
                    "formal_result_eligible": True,
                    "integrity_passed": True,
                    "passed": index % 2 == 0,
                    "capacity_score": 1.0 if index % 2 == 0 else 0.0,
                    "usage": {"total_tokens": 1000},
                    "tool_call_count": 10,
                    "elapsed_sec": 12.5,
                }
                for index, task_id in enumerate(task_ids)
            ],
        },
    )
    return task_ids


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
