from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary
from adaptive_harness.integrations.deerflow_policy import (
    DeerFlowPolicyBridge,
    FileArtifactObservationProvider,
    PublicSourceAccessObservationProvider,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.task_contract import (
    Criterion,
    CriterionKind,
    CriterionSource,
    RuleBasedTaskContractBuilder,
    TaskContract,
)
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


def _source_contract(resource: str = "http://127.0.0.1:8123/data") -> TaskContract:
    return TaskContract(
        "source-only",
        f"Open {resource} and summarize it.",
        (
            Criterion(
                id="observation:source-access",
                description="Public source was accessed",
                kind=CriterionKind.OBSERVATION_EQUALS,
                source=CriterionSource.TASK_PROMPT,
                parameters={
                    "subject": "source.access:test",
                    "expected": True,
                    "resource": resource,
                },
            ),
        ),
    )


class _StaticContractBuilder:
    def __init__(self, contract: TaskContract) -> None:
        self.contract = contract

    def build(
        self,
        task_id: str,
        task_prompt: str,
        public_schema: Any | None = None,
    ) -> TaskContract:
        return self.contract


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
    def test_required_directory_is_observed_as_an_artifact(self) -> None:
        contract = TaskContract(
            "directory-artifact",
            "Write audit files under outputs/mock_audit/.",
            (
                Criterion(
                    id="artifact:outputs-mock-audit",
                    description="Required output directory exists",
                    kind=CriterionKind.ARTIFACT_EXISTS,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"path": "outputs/mock_audit/"},
                ),
            ),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/mock_audit"
            output.mkdir(parents=True)
            (output / "slack.json").write_text("{}", encoding="utf-8")
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        self.assertEqual(len(evidence), 1)
        self.assertTrue(evidence[0].value["exists"])
        self.assertEqual(evidence[0].value["kind"], "directory")
        self.assertEqual(evidence[0].value["visible_file_count"], 1)
        self.assertFalse(evidence[0].value["visible_file_count_truncated"])
        self.assertEqual(evidence[0].value["skipped_entry_count"], 0)

    def test_required_directory_inventory_is_bounded_and_skips_symlinks(self) -> None:
        contract = TaskContract(
            "directory-artifact-bound",
            "Write audit files under outputs/audit/.",
            (
                Criterion(
                    id="artifact:outputs-audit",
                    description="Required output directory exists",
                    kind=CriterionKind.ARTIFACT_EXISTS,
                    source=CriterionSource.TASK_PROMPT,
                    parameters={"path": "outputs/audit/"},
                ),
            ),
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "outputs/audit"
            output.mkdir(parents=True)
            for index in range(FileArtifactObservationProvider.MAX_ARTIFACT_DIRECTORY_FILES + 1):
                (output / f"{index:04d}.json").write_text("{}", encoding="utf-8")
            target = root / "outside.txt"
            target.write_text("private", encoding="utf-8")
            (output / "leak.txt").symlink_to(target)
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        value = evidence[0].value
        self.assertEqual(
            value["visible_file_count"],
            FileArtifactObservationProvider.MAX_ARTIFACT_DIRECTORY_FILES,
        )
        self.assertTrue(value["visible_file_count_truncated"])
        self.assertGreaterEqual(value["skipped_entry_count"], 0)

    def test_exact_copy_of_public_provisional_file_fails_grounding(self) -> None:
        prompt = """`workspace/analysis/results.json` is a starting point, not truth.
Verify it against the raw source before writing outputs/audit.json.

```json
{"items": [{"id": "sample", "score": 1, "passed": true}]}
```
"""
        contract = _contract(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provisional = root / "workspace/analysis/results.json"
            output = root / "outputs/audit.json"
            provisional.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            payload = {"items": [{"id": "actual", "score": 2, "passed": True}]}
            provisional.write_text(json.dumps(payload), encoding="utf-8")
            output.write_text(json.dumps(payload), encoding="utf-8")
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        grounding = next(
            item for item in evidence if item.subject == "artifact.grounding:outputs/audit.json"
        )
        self.assertFalse(grounding.value)
        self.assertEqual(
            grounding.metadata["exact_provisional_copies"],
            ["workspace/analysis/results.json"],
        )

        ledger = SessionLedger("grounding-copy-feedback")
        writer = TaskEventWriter(ledger)
        writer.create_contract(contract)
        for item in evidence:
            writer.add_evidence(item)
        completion = writer.check_completion()
        self.assertTrue(
            any(
                "artifact exactly copies provisional source requiring validation" in reason
                for reason in completion.missing
            )
        )

    def test_transformed_public_provisional_file_passes_copy_guard(self) -> None:
        prompt = """`workspace/analysis/results.json` is a draft, not truth.
Re-check it against raw records before writing outputs/audit.json.

```json
{"items": [{"id": "sample", "score": 1, "passed": true}]}
```
"""
        contract = _contract(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            provisional = root / "workspace/analysis/results.json"
            output = root / "outputs/audit.json"
            provisional.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            provisional.write_text(
                json.dumps({"items": [{"id": "old", "score": 1, "passed": False}]}),
                encoding="utf-8",
            )
            output.write_text(
                json.dumps({"items": [{"id": "new", "score": 2, "passed": True}]}),
                encoding="utf-8",
            )
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        grounding = next(
            item for item in evidence if item.subject == "artifact.grounding:outputs/audit.json"
        )
        self.assertTrue(grounding.value)

    def test_workspace_scan_catches_unlisted_intermediate_copy(self) -> None:
        prompt = """Read `workspace/README.md`; its intermediate result is a starting point,
not truth. Verify it against the raw source before writing outputs/audit.json.

```json
{"items": [{"id": "sample", "score": 1, "passed": true}]}
```
"""
        contract = _contract(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "workspace/README.md"
            intermediate = root / "workspace/analysis/results.json"
            output = root / "outputs/audit.json"
            readme.parent.mkdir(parents=True)
            intermediate.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            readme.write_text("See analysis/results.json", encoding="utf-8")
            payload = {"items": [{"id": "actual", "score": 2, "passed": True}]}
            intermediate.write_text(json.dumps(payload), encoding="utf-8")
            output.write_text(json.dumps(payload), encoding="utf-8")
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        grounding = next(
            item for item in evidence if item.subject == "artifact.grounding:outputs/audit.json"
        )
        self.assertFalse(grounding.value)
        self.assertIn(
            "workspace/analysis/results.json",
            grounding.metadata["exact_provisional_copies"],
        )

    def test_oversized_provisional_source_fails_closed(self) -> None:
        prompt = """`workspace/results.json` is a draft, not truth.
Verify it against raw records before writing outputs/audit.json.

```json
{"items": [{"id": "sample"}]}
```
"""
        contract = _contract(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "workspace/results.json"
            output = root / "outputs/audit.json"
            source.parent.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            oversized = b"x" * (FileArtifactObservationProvider.MAX_GROUNDING_FILE_BYTES + 1)
            source.write_bytes(oversized)
            output.write_bytes(oversized)
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        grounding = next(
            item for item in evidence if item.subject == "artifact.grounding:outputs/audit.json"
        )
        self.assertFalse(grounding.value)
        self.assertIn(
            "provisional source not verified due to size limit: workspace/results.json",
            grounding.metadata["diagnostics"],
        )

    def test_workspace_scan_stops_at_file_limit_and_fails_closed(self) -> None:
        prompt = """`workspace/README.md` describes an intermediate starting point,
not truth. Verify it against raw records before writing outputs/audit.json.

```json
{"items": [{"id": "sample"}]}
```
"""
        contract = _contract(prompt)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            output = root / "outputs/audit.json"
            workspace.mkdir(parents=True)
            output.parent.mkdir(parents=True)
            (workspace / "README.md").write_text("draft", encoding="utf-8")
            for index in range(FileArtifactObservationProvider.MAX_GROUNDING_FILES + 1):
                (workspace / f"source-{index:04d}.txt").write_text(
                    str(index),
                    encoding="utf-8",
                )
            output.write_text('{"items": [{"id": "new"}]}', encoding="utf-8")
            evidence = FileArtifactObservationProvider(root).observe(
                contract,
                _summary(),
                turn=1,
            )

        grounding = next(
            item for item in evidence if item.subject == "artifact.grounding:outputs/audit.json"
        )
        self.assertFalse(grounding.value)
        self.assertLessEqual(
            len(grounding.metadata["checked_provisional_sources"]),
            FileArtifactObservationProvider.MAX_GROUNDING_FILES + 1,
        )
        self.assertEqual(
            grounding.metadata["skipped_provisional_sources"],
            ["<workspace scan truncated>"],
        )
        self.assertIn(
            "workspace provisional scan truncated at 512 files",
            grounding.metadata["diagnostics"],
        )

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

    def test_all_completeness_scoped_collections_empty_fails_non_vacuity(self) -> None:
        contract = _contract(
            """Write outputs/audit.json and report all matching records.

```json
{
  "items": [{"id": "sample", "score": 1, "passed": true}],
  "warnings": ["sample"]
}
```
"""
        )
        evidence = self._observe_payload(
            {"items": [], "warnings": []},
            contract=contract,
        )
        shape = next(item for item in evidence if item.subject == "artifact.json_shape:outputs/audit.json")

        self.assertFalse(shape.value)
        self.assertIn(
            "all completeness-scoped collections are empty: $.items, $.warnings",
            shape.metadata["diagnostics"],
        )

    def test_one_non_empty_completeness_scoped_collection_satisfies_non_vacuity(self) -> None:
        contract = _contract(
            """Write outputs/audit.json and report all matching records.

```json
{
  "items": [{"id": "sample", "score": 1, "passed": true}],
  "warnings": ["sample"]
}
```
"""
        )
        evidence = self._observe_payload(
            {
                "items": [{"id": "actual", "score": 2, "passed": False}],
                "warnings": [],
            },
            contract=contract,
        )
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


class PublicSourceAccessObservationProviderTests(unittest.TestCase):
    def test_turn_observation_http_failure_is_recorded_without_crashing_runtime(self) -> None:
        class FailingProvider:
            def supports(self, _criterion: Any) -> bool:
                return True

            def observe(
                self,
                _contract: Any,
                _summary: Any,
                *,
                turn: int,
            ) -> tuple[Any, ...]:
                raise OSError("HTTP Error 403: Forbidden")

        ledger = SessionLedger("turn-observation-http-error")
        bridge = DeerFlowPolicyBridge(
            contract_builder=_StaticContractBuilder(_source_contract()),
            observation_providers=(FailingProvider(),),
        )
        contract = bridge.start(
            ledger,
            task_id="source-only",
            task_prompt="Open http://127.0.0.1:8123/data",
            public_schema=None,
        )

        bridge.observe_turn(ledger, contract, _summary(), turn=1)

        failure = next(
            event.payload["failure"]
            for event in ledger.events
            if event.type == "failure/classified"
        )
        self.assertEqual(failure["error_type"], "OBSERVATION_SNAPSHOT_FAILED")
        self.assertIn("403", failure["message"])

    def test_loopback_source_is_materialized_before_completion_check(self) -> None:
        provider = PublicSourceAccessObservationProvider(
            fetch_bytes=lambda _url: (
                "http://127.0.0.1:8123/data",
                b'{"message":"ok","api_key":"secret-value"}',
            )
        )
        ledger = SessionLedger("source-materialized")
        bridge = DeerFlowPolicyBridge(
            contract_builder=_StaticContractBuilder(_source_contract()),
            observation_providers=(provider,),
        )

        bridge.start(ledger, task_id="source-only", task_prompt="Open http://127.0.0.1:8123/data", public_schema=None)

        result = TaskEventWriter(ledger).check_completion()
        self.assertTrue(result.passed)
        evidence = next(event.payload["evidence"] for event in ledger.events if event.type == "evidence/added")
        self.assertEqual(evidence["subject"], "source.access:test")
        self.assertTrue(evidence["value"])
        self.assertEqual(
            set(evidence["metadata"]),
            {"resource", "hash", "chars", "public_payload_excerpt"},
        )
        self.assertEqual(evidence["metadata"]["resource"], "http://127.0.0.1:8123/data")
        self.assertEqual(len(evidence["metadata"]["hash"]), 64)
        self.assertIn('"api_key": "<redacted>"', evidence["metadata"]["public_payload_excerpt"])

    def test_external_resource_is_not_supported_or_fetched(self) -> None:
        fetched = False

        def fetch(_url: str) -> tuple[str, bytes]:
            nonlocal fetched
            fetched = True
            return _url, b"{}"

        provider = PublicSourceAccessObservationProvider(fetch_bytes=fetch)
        evidence = provider.before_run(_source_contract("https://example.com/data"))

        self.assertEqual(evidence, ())
        self.assertFalse(fetched)

    def test_credentialed_resource_is_not_supported_or_fetched(self) -> None:
        fetched = False

        def fetch(_url: str) -> tuple[str, bytes]:
            nonlocal fetched
            fetched = True
            return _url, b"{}"

        provider = PublicSourceAccessObservationProvider(fetch_bytes=fetch)
        evidence = provider.before_run(_source_contract("http://user:pass@127.0.0.1:8123/data?token=secret"))

        self.assertEqual(evidence, ())
        self.assertFalse(fetched)

    def test_redirect_to_external_resource_fails_closed_without_evidence(self) -> None:
        provider = PublicSourceAccessObservationProvider(
            fetch_bytes=lambda _url: ("https://example.com/data", b"{}")
        )
        ledger = SessionLedger("source-redirect")
        bridge = DeerFlowPolicyBridge(
            contract_builder=_StaticContractBuilder(_source_contract()),
            observation_providers=(provider,),
        )

        bridge.start(ledger, task_id="source-only", task_prompt="Open http://127.0.0.1:8123/data", public_schema=None)
        result = TaskEventWriter(ledger).check_completion()

        self.assertFalse(result.passed)
        self.assertFalse(any(event.type == "evidence/added" for event in ledger.events))
        failure = next(event.payload["failure"] for event in ledger.events if event.type == "failure/classified")
        self.assertEqual(failure["error_type"], "OBSERVATION_SNAPSHOT_FAILED")

    def test_oversize_payload_fails_closed_without_evidence(self) -> None:
        provider = PublicSourceAccessObservationProvider(
            fetch_bytes=lambda _url: ("http://127.0.0.1:8123/data", b"x" * (64 * 1024 + 1))
        )
        ledger = SessionLedger("source-oversize")
        bridge = DeerFlowPolicyBridge(
            contract_builder=_StaticContractBuilder(_source_contract()),
            observation_providers=(provider,),
        )

        bridge.start(ledger, task_id="source-only", task_prompt="Open http://127.0.0.1:8123/data", public_schema=None)
        result = TaskEventWriter(ledger).check_completion()

        self.assertFalse(result.passed)
        self.assertFalse(any(event.type == "evidence/added" for event in ledger.events))
        failure = next(event.payload["failure"] for event in ledger.events if event.type == "failure/classified")
        self.assertIn("exceeds", failure["message"])

    def test_fetch_error_fails_closed_without_pretending_success(self) -> None:
        provider = PublicSourceAccessObservationProvider(
            fetch_bytes=lambda _url: (_ for _ in ()).throw(OSError("connection refused"))
        )
        ledger = SessionLedger("source-error")
        bridge = DeerFlowPolicyBridge(
            contract_builder=_StaticContractBuilder(_source_contract()),
            observation_providers=(provider,),
        )

        bridge.start(ledger, task_id="source-only", task_prompt="Open http://127.0.0.1:8123/data", public_schema=None)
        result = TaskEventWriter(ledger).check_completion()

        self.assertFalse(result.passed)
        self.assertFalse(any(event.type == "evidence/added" for event in ledger.events))
        failure = next(event.payload["failure"] for event in ledger.events if event.type == "failure/classified")
        self.assertEqual(failure["error_type"], "OBSERVATION_SNAPSHOT_FAILED")


if __name__ == "__main__":
    unittest.main()
