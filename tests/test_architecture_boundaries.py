from __future__ import annotations

import unittest
from pathlib import Path

from adaptive_harness.integrations.realreplica_contract import realreplica_contract_builder
from adaptive_harness.task_contract import RuleBasedTaskContractBuilder


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_core_contract_builder_does_not_infer_environment_ontology(self) -> None:
        prompt = "创建顶层标签 `VBR-52`，再建一个 `VBR-52 Harbor Stitch` 日历事件。"

        core_contract = RuleBasedTaskContractBuilder().build("generic", prompt)
        integration_contract = realreplica_contract_builder().build("realreplica", prompt)

        self.assertEqual(core_contract.criteria, ())
        self.assertEqual(
            {criterion.parameters["subject"] for criterion in integration_contract.criteria},
            {"mail.label_created", "calendar.event_created"},
        )

    def test_core_modules_do_not_contain_realreplica_business_vocabulary(self) -> None:
        root = Path(__file__).parents[1] / "src/adaptive_harness"
        core_files = (
            root / "task_contract.py",
            root / "policy_session.py",
        )
        forbidden = (
            "listing.submitted",
            "mail.label_created",
            "mail.draft_saved",
            "calendar.event_created",
            "document.updated",
            "gmail.listLabels",
            "workbench_final",
        )

        for path in core_files:
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{token!r} leaked into {path.name}")

    def test_core_policy_session_does_not_import_runtime_or_bench_integrations(self) -> None:
        root = Path(__file__).parents[1] / "src/adaptive_harness"
        source = (root / "policy_session.py").read_text(encoding="utf-8")

        self.assertNotIn("adaptive_harness.integrations.deerflow", source)
        self.assertNotIn("adaptive_harness.integrations.realreplica", source)


if __name__ == "__main__":
    unittest.main()
