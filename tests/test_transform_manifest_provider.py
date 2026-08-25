from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.integrations.deerflow_policy import DeerFlowPolicyBridge
from adaptive_harness.integrations.transform_manifest_provider import (
    TransformManifestObservationProvider,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder


class TransformManifestObservationProviderTests(unittest.TestCase):
    def test_bridge_adds_manifest_as_before_run_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/a.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "from pathlib import Path\nPath('missing.txt').read_text()\n",
                encoding="utf-8",
            )
            ledger = SessionLedger("transform-provider-bridge")
            bridge = DeerFlowPolicyBridge(
                observation_providers=(TransformManifestObservationProvider(root),)
            )

            bridge.start(
                ledger,
                task_id="transform-provider-bridge",
                task_prompt="Inspect a.py before continuing.",
                public_schema=None,
            )

        evidence = [
            event.payload["evidence"]
            for event in ledger.events
            if event.type == "evidence/added"
        ]
        self.assertEqual(len(evidence), 1)
        self.assertTrue(evidence[0]["subject"].startswith("workspace.transform_manifest:"))
        self.assertFalse(evidence[0]["metadata"]["execution_performed"])

    def test_mentioned_transform_missing_dependency_is_bounded_evidence(self) -> None:
        source = '''from pathlib import Path
SNAPSHOTS = Path(__file__).resolve().parent.parent.parent / "snapshots"
with (SNAPSHOTS / "manifest.json").open() as file:
    payload = file.read()
(Path(__file__).parent / "results.json").write_text(payload)
'''
        contract = RuleBasedTaskContractBuilder().build(
            "transform-evidence",
            "Inspect workspace/analysis/audit.py, then write outputs/report.json.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/analysis/audit.py"
            script.parent.mkdir(parents=True)
            script.write_text(source, encoding="utf-8")

            evidence = TransformManifestObservationProvider(root).before_run(contract)

        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item.value["script_path"], "workspace/analysis/audit.py")
        self.assertEqual(
            item.value["missing_reads"],
            [{"path": "snapshots/manifest.json", "exists": False, "line": 3}],
        )
        self.assertEqual(
            item.value["declared_writes"],
            [
                {
                    "path": "workspace/analysis/results.json",
                    "exists": False,
                    "line": 5,
                }
            ],
        )
        self.assertFalse(item.metadata["execution_performed"])
        self.assertLess(len(str(item.value)), 1200)

    def test_unmentioned_transform_without_required_output_is_not_injected(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "unrelated-transform",
            "Write outputs/report.json from the public records.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "workspace/unrelated.py"
            script.parent.mkdir(parents=True)
            script.write_text(
                "from pathlib import Path\nPath('missing.txt').read_text()\n",
                encoding="utf-8",
            )

            evidence = TransformManifestObservationProvider(root).before_run(contract)

        self.assertEqual(evidence, ())

    def test_unsafe_access_is_counted_without_exposing_target(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "unsafe-transform",
            "Inspect workspace/scan.py and write outputs/report.json.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            private = root / "private"
            workspace.mkdir()
            private.mkdir()
            (private / "secret_name.txt").write_text("secret", encoding="utf-8")
            (workspace / "link").symlink_to(private, target_is_directory=True)
            (workspace / "scan.py").write_text(
                "from pathlib import Path\n"
                "(Path(__file__).parent / 'link' / 'secret_name.txt').read_text()\n",
                encoding="utf-8",
            )

            evidence = TransformManifestObservationProvider(root).before_run(contract)

        value = evidence[0].value
        self.assertEqual(value["unsafe_access_count"], 1)
        self.assertEqual(value["missing_reads"], [])
        self.assertNotIn("secret_name", str(value))

    def test_evidence_count_and_payload_budget_fail_closed(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "bounded-transform",
            "Inspect a.py and b.py before writing outputs/report.json.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for name in ("a.py", "b.py"):
                (workspace / name).write_text(
                    "from pathlib import Path\nPath('missing.txt').read_text()\n",
                    encoding="utf-8",
                )
            one = TransformManifestObservationProvider(root, max_evidence=1).before_run(
                contract
            )
            none = TransformManifestObservationProvider(root, max_payload_chars=10).before_run(
                contract
            )

        self.assertEqual(len(one), 1)
        self.assertEqual(none, ())

    def test_invalid_bounds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "bounds must be positive"):
                TransformManifestObservationProvider(Path(tmp), max_evidence=0)


if __name__ == "__main__":
    unittest.main()
