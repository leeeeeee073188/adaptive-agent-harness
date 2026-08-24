"""Public-client policy bridge from DeerFlow turns into Harness task facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse
from urllib.request import urlopen

from adaptive_harness.capabilities import AcceptFinalCompletion, ModelResponse
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.policy_session import PolicySession
from adaptive_harness.progress import ProgressDetector, ProgressResult, ProgressSnapshot
from adaptive_harness.recovery import (
    RecoveryOutcomeEvaluator,
    TaskRecoveryDecision,
    TaskRecoveryExecutor,
    TaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.task_contract import (
    ContractBuilder,
    CriterionKind,
    TaskContract,
)
from adaptive_harness.task_state import (
    ContractCompletionResult,
    Evidence,
    EvidenceKind,
    EvidenceSource,
    Failure,
    TaskCompletionGate,
    TaskEventWriter,
)

if TYPE_CHECKING:
    from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary


class DeerFlowObservationProvider(Protocol):
    def supports(self, criterion: Any) -> bool: ...

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

    def supports(self, criterion: Any) -> bool:
        return criterion.kind is CriterionKind.ARTIFACT_EXISTS

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


class OutputFileCountObservationProvider:
    """Count visible output files for an explicit public `exactly N files` criterion."""

    def __init__(self, task_root: Path) -> None:
        self.outputs = (task_root.resolve() / "outputs").resolve()

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.EXACT_COUNT
            and criterion.parameters.get("subject") == "files"
        )

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        if not any(self.supports(criterion) for criterion in contract.criteria):
            return ()
        count = (
            sum(
                path.is_file()
                and not any(part.startswith(".") for part in path.relative_to(self.outputs).parts)
                for path in self.outputs.rglob("*")
            )
            if self.outputs.is_dir()
            else 0
        )
        return (
            Evidence(
                f"deerflow:t{turn}:output-file-count",
                EvidenceKind.COUNT,
                "files",
                count,
                EvidenceSource.ARTIFACT_INSPECTION,
                {"provider": "output-file-count", "root": "outputs/"},
            ),
        )


class StructuredDeerFlowObservationProvider:
    """Read explicit evidence attached by tools; never interpret result prose."""

    def __init__(self, *, assume_all: bool = False) -> None:
        self.assume_all = assume_all

    def supports(self, criterion: Any) -> bool:
        return self.assume_all

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

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject") == self.subject
        )

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
        unsupported_criteria: str = "reject",
        recovery_policy: TaskRecoveryPolicy | None = None,
        recovery_executor: TaskRecoveryExecutor | None = None,
        progress_detector: ProgressDetector | None = None,
        recovery_outcome_evaluator: RecoveryOutcomeEvaluator | None = None,
        resource_guardrail: ResourceGuardrail | None = None,
        policy_session: PolicySession | None = None,
        allow_unverified_completion: bool = False,
    ) -> None:
        self.observation_providers = tuple(observation_providers)
        self.policy_session = policy_session or PolicySession(
            response_completion_policy=AcceptFinalCompletion(),
            contract_builder=contract_builder,
            completion_gate=completion_gate,
            max_completion_turns=max_completion_turns,
            unsupported_criteria=unsupported_criteria,
            criterion_supported=self._criterion_supported,
            recovery_policy=recovery_policy,
            recovery_executor=recovery_executor,
            progress_detector=progress_detector,
            recovery_outcome_evaluator=recovery_outcome_evaluator,
            resource_guardrail=resource_guardrail,
            allow_unverified_completion=allow_unverified_completion,
        )
        if (
            policy_session is not None
            and max_completion_turns != policy_session.max_completion_turns
        ):
            raise ValueError(
                "max_completion_turns must match the injected PolicySession"
            )

    @property
    def max_completion_turns(self) -> int:
        return self.policy_session.max_completion_turns

    @property
    def unsupported_criteria(self) -> str:
        return self.policy_session.unsupported_criteria

    def prepare_context(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        environment_state: Mapping[str, Any],
        task_state: Mapping[str, Any],
    ) -> Any:
        return self.policy_session.prepare_context(
            messages,
            environment_state=environment_state,
            task_state=task_state,
        )

    def start(
        self,
        ledger: SessionLedger,
        *,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, object] | None,
    ) -> TaskContract:
        contract = self.policy_session.start_contract(
            ledger,
            task_id=task_id,
            task_prompt=task_prompt,
            public_schema=public_schema,
            criterion_supported=self._criterion_supported,
        )
        writer = TaskEventWriter(ledger)
        for provider_index, provider in enumerate(self.observation_providers):
            before_run = getattr(provider, "before_run", None)
            if before_run is None:
                continue
            try:
                before_run(contract)
            except (KeyError, TypeError, ValueError) as error:
                writer.classify_failure(
                    Failure(
                        id=f"deerflow:before-run-provider:{provider_index + 1}",
                        error_type="OBSERVATION_SNAPSHOT_FAILED",
                        message=str(error),
                        source=EvidenceSource.RUNTIME_OBSERVATION,
                        metadata={"provider": type(provider).__name__},
                    )
        )
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

    def begin_turn(self, ledger: SessionLedger) -> ProgressSnapshot | None:
        return self.policy_session.begin_turn(ledger)

    def check_progress(
        self,
        ledger: SessionLedger,
        before: ProgressSnapshot | None,
    ) -> ProgressResult | None:
        return self.policy_session.check_progress(ledger, before)

    def check_resources(
        self,
        ledger: SessionLedger,
        progress: ProgressResult | None,
        *,
        turn: int,
    ) -> tuple[dict[str, object], ...]:
        return self.policy_session.check_resources(ledger, progress, turn=turn)

    def check_completion(
        self,
        ledger: SessionLedger,
        *,
        response_text: str | None = None,
    ) -> tuple[ContractCompletionResult, str | None, TaskRecoveryDecision | None]:
        return self.policy_session.check_completion(
            ledger,
            response=(ModelResponse(content=response_text) if response_text is not None else None),
        )

    def _criterion_supported(self, criterion: Any) -> bool:
        return any(
            bool(getattr(provider, "supports", lambda _criterion: False)(criterion))
            for provider in self.observation_providers
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
