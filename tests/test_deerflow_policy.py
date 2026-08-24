from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary
from adaptive_harness.integrations.deerflow_policy import DeerFlowPolicyBridge, FileArtifactObservationProvider
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.task_contract import CriterionKind, RuleBasedTaskContractBuilder
from adaptive_harness.task_state import TaskEventWriter


def _contract(prompt: str | None = None) -> Any:
    return RuleBasedTaskContractBuilder().build(
        "json-shape",
        prompt
        or """Write outputs/audit.json.

```json
{
  "summary": "short text",
  "items": [
    {"id": "a", "score": 1, "passed": true}
  ]
}
```
""",
    )


def _slug_brand_contract() -> Any:
    contract = _contract(
        """Write outputs/audit.json.

```json
{
  "items": [
    {"slug": "sample-slug", "brand": "sample-brand", "price": 1}
  ]
}
```
"""
    )
    shape = next(
        item
        for item in contract.criteria
        if item.parameters.get("subject") == "artifact.json_shape:outputs/audit.json"
    )
    encoded = json.dumps(shape.parameters, ensure_ascii=False)
    assert "sample-slug" not in encoded
    assert "sample-brand" not in encoded
    assert shape.parameters["list_identity_keys"] == {"$.items": ["slug", "brand"]}
    return contract


def _summary() -> DeerFlowReplaySummary:
    return DeerFlowReplaySummary(
        response_text="done",
        tool_calls=(),
        tool_results=(),
        usage={},
        source_event_count=0,
        canonical_event_count=0,
    )


class FileArtifactJsonShapeObservationTests(unittest.TestCase):
    def test_valid_json_shape_emits_satisfied_consistency_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/audit.json"
            output.parent.mkdir()
            output.write_text(
                json.dumps({"summary": "ok", "items": [{"id": "x", "score": 2, "passed": False}]}),
                encoding="utf-8",
            )
            evidence = FileArtifactObservationProvider(root).observe(_contract(), _summary(), turn=1)

        shape = [item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json"]
        self.assertEqual(shape[-1].value, True)
        self.assertEqual(shape[-1].metadata["diagnostics"], [])

    def test_missing_required_key_fails_shape(self) -> None:
        evidence = self._observe_payload({"items": [{"id": "x", "score": 2, "passed": True}]})
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertFalse(shape.value)
        self.assertIn("missing required key $.summary", shape.metadata["diagnostics"])

    def test_wrong_type_fails_shape(self) -> None:
        evidence = self._observe_payload({"summary": "ok", "items": [{"id": "x", "score": "2", "passed": True}]})
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertFalse(shape.value)
        self.assertIn("wrong type $.items[0].score: expected integer, observed string", shape.metadata["diagnostics"])

    def test_duplicate_list_identity_fails_consistency(self) -> None:
        evidence = self._observe_payload(
            {
                "summary": "ok",
                "items": [
                    {"id": "x", "score": 1, "passed": True},
                    {"id": "x", "score": 2, "passed": True},
                ],
            }
        )
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertFalse(shape.value)
        self.assertIn("duplicate identity $.items (id)=('x',)", shape.metadata["diagnostics"])

    def test_same_slug_with_different_brand_is_allowed(self) -> None:
        evidence = self._observe_payload(
            {
                "items": [
                    {"slug": "shoe", "brand": "alpha", "price": 1},
                    {"slug": "shoe", "brand": "beta", "price": 2},
                ]
            },
            contract=_slug_brand_contract(),
        )
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertTrue(shape.value)

    def test_same_slug_and_brand_duplicate_is_rejected(self) -> None:
        evidence = self._observe_payload(
            {
                "items": [
                    {"slug": "shoe", "brand": "alpha", "price": 1},
                    {"slug": "shoe", "brand": "alpha", "price": 2},
                ]
            },
            contract=_slug_brand_contract(),
        )
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertFalse(shape.value)
        self.assertIn("duplicate identity $.items (slug,brand)=('shoe', 'alpha')", shape.metadata["diagnostics"])

    def test_empty_list_is_valid_even_when_example_is_non_empty(self) -> None:
        evidence = self._observe_payload({"summary": "ok", "items": []})
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertTrue(shape.value)

    def test_malformed_json_fails_shape_without_crashing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/audit.json"
            output.parent.mkdir()
            output.write_text("not json", encoding="utf-8")
            evidence = FileArtifactObservationProvider(root).observe(_contract(), _summary(), turn=1)

        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")
        self.assertFalse(shape.value)
        self.assertTrue(any(item.startswith("invalid json") for item in shape.metadata["diagnostics"]))

    def test_completion_rejects_existing_artifact_with_bad_shape(self) -> None:
        contract = _contract()
        ledger = SessionLedger("shape-check")
        writer = TaskEventWriter(ledger)
        writer.create_contract(contract)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/audit.json"
            output.parent.mkdir()
            output.write_text(
                json.dumps({"summary": "ok", "items": [{"id": "x", "score": "bad", "passed": True}]}),
                encoding="utf-8",
            )
            for item in FileArtifactObservationProvider(root).observe(contract, _summary(), turn=1):
                writer.add_evidence(item)

        result = writer.check_completion()
        self.assertFalse(result.passed)
        self.assertTrue(any("artifact.json_shape" in item for item in result.missing))

    def _observe_payload(self, payload: object, *, contract: Any | None = None) -> tuple[Any, ...]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/audit.json"
            output.parent.mkdir()
            output.write_text(json.dumps(payload), encoding="utf-8")
            return tuple(FileArtifactObservationProvider(root).observe(contract or _contract(), _summary(), turn=1))


class DeerFlowPolicyBridgeSourceCriterionTests(unittest.TestCase):
    def test_core_handled_public_source_access_criterion_is_supported(self) -> None:
        contract = RuleBasedTaskContractBuilder().build(
            "source",
            "Open https://public.example.com/data and then write outputs/report.md.",
        )
        criterion = next(
            item
            for item in contract.criteria
            if item.kind is CriterionKind.OBSERVATION_EQUALS
            and str(item.parameters.get("subject") or "").startswith("source.access")
        )

        bridge = DeerFlowPolicyBridge(unsupported_criteria="observe_only")

        self.assertTrue(bridge._criterion_supported(criterion))


if __name__ == "__main__":
    unittest.main()
