"""Public-client policy bridge from DeerFlow turns into Harness task facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse
from urllib.request import urlopen

from adaptive_harness.ledger import SessionLedger
from adaptive_harness.task_contract import (
    ContractBuilder,
    CriterionKind,
    RuleBasedTaskContractBuilder,
    TaskContract,
)
from adaptive_harness.task_state import (
    ContractCompletionResult,
    Evidence,
    EvidenceCompletionGate,
    EvidenceKind,
    EvidenceSource,
    Failure,
    TaskCompletionGate,
    TaskEventWriter,
    TaskStateProjector,
)

if TYPE_CHECKING:
    from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary


class DeerFlowObservationProvider(Protocol):
    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]: ...


class FileArtifactObservationProvider:
    """Verify public artifact criteria against an isolated task root."""

    def __init__(self, task_root: Path) -> None:
        self.task_root = task_root.resolve()

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        evidence = []
        for criterion in contract.criteria:
            if criterion.kind is not CriterionKind.ARTIFACT_EXISTS:
                continue
            relative = str(criterion.parameters["path"]).removeprefix("/task/")
            path = (self.task_root / relative).resolve()
            if not path.is_relative_to(self.task_root):
                raise ValueError(f"artifact criterion escapes task root: {relative}")
            exists = path.is_file()
            value = {
                "exists": exists,
                "size": path.stat().st_size if exists else None,
                "sha256": _file_sha256(path) if exists else None,
            }
            evidence.append(
                Evidence(
                    id=f"deerflow:t{turn}:{criterion.id}",
                    kind=EvidenceKind.ARTIFACT,
                    subject=relative,
                    value=value,
                    source=EvidenceSource.ARTIFACT_INSPECTION,
                    metadata={"provider": "filesystem"},
                )
            )
        return tuple(evidence)


class StructuredDeerFlowObservationProvider:
    """Read explicit evidence attached by tools; never interpret result prose."""

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        evidence = []
        for result_index, result in enumerate(summary.tool_results):
            artifact = result.get("artifact")
            if not isinstance(artifact, Mapping):
                continue
            raw_evidence = artifact.get("adaptive_evidence") or artifact.get("evidence")
            if raw_evidence is None:
                continue
            if not isinstance(raw_evidence, list):
                raise ValueError("DeerFlow tool artifact evidence must be a list")
            for evidence_index, raw in enumerate(raw_evidence):
                if not isinstance(raw, Mapping):
                    raise ValueError("DeerFlow tool artifact evidence entries must be objects")
                evidence.append(
                    Evidence(
                        id=str(
                            raw.get("id")
                            or f"deerflow:t{turn}:r{result_index + 1}:e{evidence_index + 1}"
                        ),
                        kind=EvidenceKind(raw["kind"]),
                        subject=str(raw["subject"]),
                        value=raw.get("value"),
                        source=EvidenceSource.TOOL_RESULT,
                        metadata={"provider": "deerflow-tool-artifact"},
                    )
                )
        return tuple(evidence)


class HttpJsonMatchObservationProvider:
    """Project a public loopback state signal into one observation criterion."""

    def __init__(
        self,
        *,
        url: str,
        subject: str,
        match_kind: str,
        match_field: str,
        match_value: str,
        fetch_json: Callable[[str], Any] | None = None,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("HTTP observation providers are restricted to loopback http endpoints")
        if match_kind != "list_any_field_equals":
            raise ValueError(f"unsupported HTTP observation match kind: {match_kind}")
        self.url = url
        self.subject = subject
        self.match_kind = match_kind
        self.match_field = match_field
        self.match_value = match_value
        self.fetch_json = fetch_json or _fetch_json

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        body = self.fetch_json(self.url)
        matched = bool(
            isinstance(body, list)
            and any(
                isinstance(item, Mapping)
                and str(item.get(self.match_field)) == self.match_value
                for item in body
            )
        )
        return (
            Evidence(
                id=f"deerflow:t{turn}:http:{self.subject}",
                kind=EvidenceKind.OBSERVATION,
                subject=self.subject,
                value=matched,
                source=EvidenceSource.RUNTIME_OBSERVATION,
                metadata={
                    "provider": "loopback-http-json",
                    "match_kind": self.match_kind,
                    "match_field": self.match_field,
                },
            ),
        )


class DeerFlowPolicyBridge:
    """Evaluate each embedded-client turn and request continuation when needed."""

    def __init__(
        self,
        *,
        contract_builder: ContractBuilder | None = None,
        completion_gate: TaskCompletionGate | None = None,
        observation_providers: Sequence[DeerFlowObservationProvider] = (),
        max_completion_turns: int = 2,
    ) -> None:
        if max_completion_turns < 1:
            raise ValueError("max_completion_turns must be at least one")
        self.contract_builder = contract_builder or RuleBasedTaskContractBuilder()
        self.completion_gate = completion_gate or EvidenceCompletionGate()
        self.observation_providers = tuple(observation_providers)
        self.max_completion_turns = max_completion_turns

    def start(
        self,
        ledger: SessionLedger,
        *,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, object] | None,
    ) -> TaskContract:
        contract = self.contract_builder.build(task_id, task_prompt, public_schema)
        TaskEventWriter(ledger).create_contract(contract)
        return contract

    def observe_turn(
        self,
        ledger: SessionLedger,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> None:
        writer = TaskEventWriter(ledger)
        for provider_index, provider in enumerate(self.observation_providers):
            try:
                for evidence in provider.observe(contract, summary, turn=turn):
                    writer.add_evidence(evidence)
            except (KeyError, TypeError, ValueError) as error:
                writer.classify_failure(
                    Failure(
                        id=f"deerflow:t{turn}:observation-provider:{provider_index + 1}",
                        error_type="INVALID_EVIDENCE",
                        message=str(error),
                        source=EvidenceSource.RUNTIME_OBSERVATION,
                        metadata={"provider": type(provider).__name__},
                    )
                )

    def check_completion(self, ledger: SessionLedger) -> tuple[ContractCompletionResult, str | None]:
        state = TaskStateProjector().project(ledger.events)
        result = self.completion_gate.verify(state)
        feedback = None if result.passed else self.completion_gate.feedback(result)
        ledger.append("completion/checked", {**result.to_payload(), "feedback": feedback})
        return result, feedback


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
