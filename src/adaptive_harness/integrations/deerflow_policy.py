"""Public-client policy bridge from DeerFlow turns into Harness task facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from adaptive_harness.ledger import SessionLedger
from adaptive_harness.recovery import (
    TaskFailureCategory,
    TaskFailureContext,
    TaskRecoveryAction,
    TaskRecoveryDecision,
    TaskRecoveryExecutor,
    TaskRecoveryPolicy,
)
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
    RecoveryExecutionRecord,
    RecoveryRecord,
    TaskCompletionGate,
    TaskEventWriter,
    TaskStateProjector,
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


class GmailMcpObservationProvider:
    """Verify public label/event postconditions through read-only MCP tools."""

    def __init__(self, endpoint: str, *, call_tool: Callable[..., Any] | None = None) -> None:
        _validate_loopback_endpoint(endpoint)
        self.endpoint = endpoint
        self.call_tool = call_tool or _mcp_call

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject")
            in {"mail.label_created", "calendar.event_created"}
            and bool(criterion.parameters.get("target"))
        )

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        evidence = []
        for criterion in contract.criteria:
            if not self.supports(criterion):
                continue
            subject = str(criterion.parameters["subject"])
            target = str(criterion.parameters["target"])
            if subject == "mail.label_created":
                payload = self.call_tool(self.endpoint, "gmail.listLabels", {})
                matched = any(
                    isinstance(item, Mapping) and item.get("name") == target
                    for item in payload.get("labels", [])
                )
            else:
                payload = self.call_tool(self.endpoint, "calendar.listEvents", {})
                matched = any(
                    isinstance(item, Mapping)
                    and (item.get("title") == target or item.get("summary") == target)
                    for item in payload.get("events", [])
                )
            evidence.append(
                Evidence(
                    f"deerflow:t{turn}:mcp:{subject}",
                    EvidenceKind.OBSERVATION,
                    subject,
                    matched,
                    EvidenceSource.RUNTIME_OBSERVATION,
                    {"provider": "gmail-mcp-read", "target": target},
                )
            )
        return tuple(evidence)


class GoogleDocsMcpChangeObservationProvider:
    """Require the public target document content to change during the run."""

    def __init__(self, endpoint: str, *, call_tool: Callable[..., Any] | None = None) -> None:
        _validate_loopback_endpoint(endpoint)
        self.endpoint = endpoint
        self.call_tool = call_tool or _mcp_call
        self._before: dict[str, str] = {}

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject") == "document.updated"
            and bool(criterion.parameters.get("target"))
        )

    def before_run(self, contract: TaskContract) -> None:
        for criterion in contract.criteria:
            if self.supports(criterion):
                target = str(criterion.parameters["target"])
                self._before[target] = self._document_hash(target)

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        evidence = []
        for criterion in contract.criteria:
            if not self.supports(criterion):
                continue
            target = str(criterion.parameters["target"])
            before = self._before.get(target)
            after = self._document_hash(target)
            evidence.append(
                Evidence(
                    f"deerflow:t{turn}:mcp:document.updated",
                    EvidenceKind.OBSERVATION,
                    "document.updated",
                    before is not None and before != after,
                    EvidenceSource.RUNTIME_OBSERVATION,
                    {
                        "provider": "google-docs-mcp-read",
                        "target": target,
                        "before_sha256": before,
                        "after_sha256": after,
                    },
                )
            )
        return tuple(evidence)

    def _document_hash(self, title: str) -> str:
        escaped = title.replace("'", "\\'")
        listing = self.call_tool(
            self.endpoint,
            "search_docs",
            {"q": f"name = '{escaped}'", "pageSize": 10},
        )
        files = listing.get("files") or []
        matches = [item for item in files if isinstance(item, Mapping) and item.get("name") == title]
        if len(matches) != 1:
            raise ValueError(f"expected one public target document named {title!r}")
        document = self.call_tool(
            self.endpoint,
            "docs.documents.get",
            {"documentId": matches[0]["id"]},
        )
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

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
    ) -> None:
        if max_completion_turns < 1:
            raise ValueError("max_completion_turns must be at least one")
        if unsupported_criteria not in {"reject", "observe_only"}:
            raise ValueError("unsupported_criteria must be 'reject' or 'observe_only'")
        self.contract_builder = contract_builder or RuleBasedTaskContractBuilder()
        self.completion_gate = completion_gate or EvidenceCompletionGate()
        self.observation_providers = tuple(observation_providers)
        self.max_completion_turns = max_completion_turns
        self.unsupported_criteria = unsupported_criteria
        self.recovery_policy = recovery_policy
        self.recovery_executor = recovery_executor

    def start(
        self,
        ledger: SessionLedger,
        *,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, object] | None,
    ) -> TaskContract:
        contract = self.contract_builder.build(task_id, task_prompt, public_schema)
        if self.unsupported_criteria == "observe_only":
            criteria = tuple(
                criterion
                if not criterion.required or self._criterion_supported(criterion)
                else replace(criterion, required=False)
                for criterion in contract.criteria
            )
            contract = TaskContract(
                contract.task_id,
                contract.original_request,
                criteria,
                contract.public_schema_hash,
            )
        TaskEventWriter(ledger).create_contract(contract)
        ledger.append(
            "policy/configured",
            {
                "completion_gate": "evidence",
                "unsupported_criteria": self.unsupported_criteria,
                "enforced_criterion_ids": [
                    criterion.id for criterion in contract.criteria if criterion.required
                ],
                "observe_only_criterion_ids": [
                    criterion.id for criterion in contract.criteria if not criterion.required
                ],
            },
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

    def check_completion(
        self,
        ledger: SessionLedger,
    ) -> tuple[ContractCompletionResult, str | None, TaskRecoveryDecision | None]:
        state = TaskStateProjector().project(ledger.events)
        if (
            self.unsupported_criteria == "observe_only"
            and state.contract is not None
            and not any(criterion.required for criterion in state.contract.criteria)
        ):
            result = ContractCompletionResult(
                True,
                (),
                (),
                "No provider-backed criteria; completion gate is observe-only.",
            )
        else:
            result = self.completion_gate.verify(state)
        feedback = None if result.passed else self.completion_gate.feedback(result)
        ledger.append("completion/checked", {**result.to_payload(), "feedback": feedback})
        recovery = None
        if not result.passed and self.recovery_policy is not None:
            context = self._failure_context(state, result)
            recovery = self.recovery_policy.decide(context)
            TaskEventWriter(ledger).record_recovery(
                RecoveryRecord(context.primary, context.secondary, recovery)
            )
            actions = ", ".join(action.value for action in recovery.actions)
            feedback = f"{feedback}\nRecovery actions: {actions}."
            if self.recovery_executor is not None:
                execution = self.recovery_executor.execute(
                    recovery,
                    missing=result.missing,
                )
                TaskEventWriter(ledger).record_recovery_execution(
                    RecoveryExecutionRecord(execution)
                )
                if execution.directives:
                    feedback = f"{feedback}\n" + "\n".join(execution.directives)
        return result, feedback, recovery

    def _criterion_supported(self, criterion: Any) -> bool:
        return any(
            bool(getattr(provider, "supports", lambda _criterion: False)(criterion))
            for provider in self.observation_providers
        )

    def _failure_context(
        self,
        state: Any,
        result: ContractCompletionResult,
    ) -> TaskFailureContext:
        by_id = {
            criterion.id: criterion
            for criterion in (state.contract.criteria if state.contract is not None else ())
        }
        failed_kinds = {
            by_id[assessment.criterion_id].kind
            for assessment in result.assessments
            if assessment.status.value != "satisfied" and assessment.criterion_id in by_id
        }
        primary = (
            TaskFailureCategory.ARTIFACT_ERROR
            if CriterionKind.ARTIFACT_EXISTS in failed_kinds
            else TaskFailureCategory.CONSTRAINT_MISS
            if CriterionKind.EXACT_COUNT in failed_kinds
            else TaskFailureCategory.STATE_INCONSISTENCY
            if CriterionKind.OBSERVATION_EQUALS in failed_kinds
            else TaskFailureCategory.PREMATURE_FINISH
        )
        attempts: dict[TaskRecoveryAction, int] = {}
        for record in state.recoveries:
            for action in record.decision.actions:
                attempts[action] = attempts.get(action, 0) + 1
        return TaskFailureContext(
            primary,
            (TaskFailureCategory.PREMATURE_FINISH,),
            attempts=attempts,
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


def _validate_loopback_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("MCP observation providers are restricted to loopback http endpoints")


def _mcp_call(endpoint: str, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"adaptive-observe:{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            }
        ).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise ValueError(f"MCP observation call failed: {name}") from error
    if not isinstance(envelope, Mapping) or envelope.get("error"):
        raise ValueError(f"MCP observation returned an error: {name}")
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        raise ValueError(f"MCP observation result is malformed: {name}")
    for block in result.get("content") or ():
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise ValueError(f"MCP observation contains no structured text payload: {name}")
