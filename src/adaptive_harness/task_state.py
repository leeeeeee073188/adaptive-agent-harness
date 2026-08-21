"""Durable task facts and pure TaskState reconstruction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from adaptive_harness.capabilities import ToolResult
from adaptive_harness.ledger import SessionEvent, SessionLedger
from adaptive_harness.task_contract import Criterion, CriterionKind, TaskContract

TASK_CONTRACT_CREATED = "task/contract-created"
STATE_UPDATED = "state/updated"
EVIDENCE_ADDED = "evidence/added"
FAILURE_CLASSIFIED = "failure/classified"
COMPLETION_CHECKED = "completion/checked"


class EvidenceKind(StrEnum):
    ARTIFACT = "artifact"
    COUNT = "count"
    OBSERVATION = "observation"


class EvidenceSource(StrEnum):
    RUNTIME_OBSERVATION = "runtime_observation"
    TOOL_RESULT = "tool_result"
    ARTIFACT_INSPECTION = "artifact_inspection"


class CriterionStatus(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: EvidenceKind
    subject: str
    value: Any
    source: EvidenceSource
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "subject": self.subject,
            "value": self.value,
            "source": self.source.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Evidence:
        return cls(
            id=str(payload["id"]),
            kind=EvidenceKind(payload["kind"]),
            subject=str(payload["subject"]),
            value=payload.get("value"),
            source=EvidenceSource(payload["source"]),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class Failure:
    id: str
    error_type: str
    message: str
    source: EvidenceSource
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "error_type": self.error_type,
            "message": self.message,
            "source": self.source.value,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Failure:
        return cls(
            id=str(payload["id"]),
            error_type=str(payload["error_type"]),
            message=str(payload["message"]),
            source=EvidenceSource(payload["source"]),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CriterionAssessment:
    criterion_id: str
    status: CriterionStatus
    reason: str
    evidence_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status.value,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> CriterionAssessment:
        return cls(
            criterion_id=str(payload["criterion_id"]),
            status=CriterionStatus(payload["status"]),
            reason=str(payload["reason"]),
            evidence_ids=tuple(str(item) for item in payload.get("evidence_ids") or ()),
        )


@dataclass(frozen=True)
class ContractCompletionResult:
    passed: bool
    assessments: tuple[CriterionAssessment, ...]
    missing: tuple[str, ...]
    reason: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "assessments": [assessment.to_payload() for assessment in self.assessments],
            "missing": list(self.missing),
            "reason": self.reason,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ContractCompletionResult:
        return cls(
            passed=bool(payload["passed"]),
            assessments=tuple(
                CriterionAssessment.from_payload(item) for item in payload.get("assessments") or ()
            ),
            missing=tuple(str(item) for item in payload.get("missing") or ()),
            reason=str(payload.get("reason") or ""),
        )


@dataclass(frozen=True)
class TaskState:
    contract: TaskContract | None = None
    values: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    failures: tuple[Failure, ...] = ()
    completion_checks: tuple[ContractCompletionResult, ...] = ()

    @property
    def latest_completion(self) -> ContractCompletionResult | None:
        return self.completion_checks[-1] if self.completion_checks else None

    def to_context(self, *, evidence_limit: int = 20, failure_limit: int = 10) -> dict[str, Any]:
        """Return a bounded model working set while the ledger retains full facts."""

        if evidence_limit < 0 or failure_limit < 0:
            raise ValueError("context limits must be non-negative")
        evidence_window = self.evidence[-evidence_limit:] if evidence_limit else ()
        failure_window = self.failures[-failure_limit:] if failure_limit else ()
        return {
            "task_id": self.contract.task_id if self.contract else None,
            "criteria": (
                [criterion.to_payload() for criterion in self.contract.criteria]
                if self.contract
                else []
            ),
            "values": dict(self.values),
            "evidence": [
                {**item.to_payload(), "value": _compact_value(item.value)}
                for item in evidence_window
            ],
            "failures": [
                {
                    **item.to_payload(),
                    "message": _compact_text(item.message, 300),
                    "metadata": _compact_value(item.metadata),
                }
                for item in failure_window
            ],
            "latest_completion": self.latest_completion.to_payload() if self.latest_completion else None,
        }


class TaskStateProjector:
    """Rebuild TaskState from durable events with no retained mutable state."""

    def project(self, events: Iterable[SessionEvent]) -> TaskState:
        contract: TaskContract | None = None
        values: dict[str, Any] = {}
        evidence: list[Evidence] = []
        evidence_ids: set[str] = set()
        failures: list[Failure] = []
        failure_ids: set[str] = set()
        completion_checks: list[ContractCompletionResult] = []

        for event in events:
            if event.type == TASK_CONTRACT_CREATED:
                if contract is not None:
                    raise ValueError("a ledger may contain only one task contract")
                contract = TaskContract.from_payload(event.payload["contract"])
            elif event.type == STATE_UPDATED:
                delta = event.payload.get("delta")
                if not isinstance(delta, Mapping):
                    raise ValueError("state/updated requires an object delta")
                values.update(delta)
            elif event.type == EVIDENCE_ADDED:
                item = Evidence.from_payload(event.payload["evidence"])
                if item.id in evidence_ids:
                    raise ValueError(f"duplicate evidence id: {item.id!r}")
                evidence_ids.add(item.id)
                evidence.append(item)
            elif event.type == FAILURE_CLASSIFIED:
                item = Failure.from_payload(event.payload["failure"])
                if item.id in failure_ids:
                    raise ValueError(f"duplicate failure id: {item.id!r}")
                failure_ids.add(item.id)
                failures.append(item)
            elif event.type == COMPLETION_CHECKED and "assessments" in event.payload:
                completion_checks.append(ContractCompletionResult.from_payload(event.payload))

        return TaskState(contract, values, tuple(evidence), tuple(failures), tuple(completion_checks))


class RuleBasedContractChecker:
    """Evaluate public criteria only against runtime evidence."""

    def check(self, state: TaskState) -> ContractCompletionResult:
        if state.contract is None:
            return ContractCompletionResult(False, (), ("task contract",), "Task contract is missing.")
        required = [item for item in state.contract.criteria if item.required]
        if not required:
            return ContractCompletionResult(
                False,
                (),
                ("verifiable criteria",),
                "Task contract has no required verifiable criteria.",
            )

        by_id = {item.id: item for item in state.contract.criteria}
        assessments: dict[str, CriterionAssessment] = {}

        def assess(criterion: Criterion) -> CriterionAssessment:
            if criterion.id in assessments:
                return assessments[criterion.id]
            dependency_results = [assess(by_id[dependency]) for dependency in criterion.depends_on]
            blocked = [item.criterion_id for item in dependency_results if item.status is not CriterionStatus.SATISFIED]
            if blocked:
                result = CriterionAssessment(
                    criterion.id,
                    CriterionStatus.BLOCKED,
                    f"Blocked by unsatisfied dependencies: {', '.join(blocked)}",
                )
            else:
                result = self._assess_own_requirement(criterion, state.evidence)
            assessments[criterion.id] = result
            return result

        ordered = tuple(assess(criterion) for criterion in state.contract.criteria)
        required_results = [assessments[item.id] for item in required]
        missing = tuple(item.reason for item in required_results if item.status is not CriterionStatus.SATISFIED)
        passed = not missing
        return ContractCompletionResult(
            passed,
            ordered,
            missing,
            "All required criteria are satisfied." if passed else "Required criteria are not satisfied.",
        )

    def _assess_own_requirement(
        self,
        criterion: Criterion,
        evidence: tuple[Evidence, ...],
    ) -> CriterionAssessment:
        if criterion.kind is CriterionKind.DEPENDENCY:
            return CriterionAssessment(
                criterion.id,
                CriterionStatus.SATISFIED,
                "All dependencies are satisfied.",
            )
        if criterion.kind is CriterionKind.ARTIFACT_EXISTS:
            path = _normalize_subject(str(criterion.parameters["path"]))
            matches = [
                item
                for item in evidence
                if item.kind is EvidenceKind.ARTIFACT and _normalize_subject(item.subject) == path
            ]
            if not matches:
                return CriterionAssessment(
                    criterion.id,
                    CriterionStatus.PENDING,
                    f"Missing artifact evidence: {path}",
                )
            latest = matches[-1]
            exists = latest.value.get("exists") if isinstance(latest.value, Mapping) else latest.value
            status = CriterionStatus.SATISFIED if exists is True else CriterionStatus.UNSATISFIED
            reason = f"Artifact exists: {path}" if exists is True else f"Artifact does not exist: {path}"
            return CriterionAssessment(criterion.id, status, reason, (latest.id,))
        if criterion.kind is CriterionKind.EXACT_COUNT:
            subject = _normalize_subject(str(criterion.parameters["subject"]))
            expected = criterion.parameters["expected"]
            matches = [
                item
                for item in evidence
                if item.kind is EvidenceKind.COUNT and _normalize_subject(item.subject) == subject
            ]
            if not matches:
                return CriterionAssessment(
                    criterion.id,
                    CriterionStatus.PENDING,
                    f"Missing count evidence for {subject}; expected exactly {expected}",
                )
            latest = matches[-1]
            observed = latest.value
            satisfied = isinstance(observed, int) and not isinstance(observed, bool) and observed == expected
            status = CriterionStatus.SATISFIED if satisfied else CriterionStatus.UNSATISFIED
            reason = f"Observed exactly {expected} {subject}" if satisfied else (
                f"Expected exactly {expected} {subject}, observed {observed!r}"
            )
            return CriterionAssessment(criterion.id, status, reason, (latest.id,))
        if criterion.kind is CriterionKind.OBSERVATION_EQUALS:
            subject = _normalize_subject(str(criterion.parameters["subject"]))
            expected = criterion.parameters.get("expected")
            matches = [
                item
                for item in evidence
                if item.kind is EvidenceKind.OBSERVATION and _normalize_subject(item.subject) == subject
            ]
            if not matches:
                return CriterionAssessment(
                    criterion.id,
                    CriterionStatus.PENDING,
                    f"Missing runtime observation: {subject}",
                )
            latest = matches[-1]
            satisfied = latest.value == expected
            status = CriterionStatus.SATISFIED if satisfied else CriterionStatus.UNSATISFIED
            reason = f"Observed required value for {subject}" if satisfied else (
                f"Expected {subject}={expected!r}, observed {latest.value!r}"
            )
            return CriterionAssessment(criterion.id, status, reason, (latest.id,))
        raise AssertionError(f"unhandled criterion kind: {criterion.kind}")


class TaskCompletionGate(Protocol):
    def verify(self, state: TaskState) -> ContractCompletionResult: ...

    def feedback(self, result: ContractCompletionResult, *, limit: int = 3) -> str: ...


class EvidenceCompletionGate:
    """Opt-in A3 gate; replacing or removing its service gives a clean ablation."""

    def __init__(self, checker: RuleBasedContractChecker | None = None) -> None:
        self.checker = checker or RuleBasedContractChecker()

    def verify(self, state: TaskState) -> ContractCompletionResult:
        return self.checker.check(state)

    def feedback(self, result: ContractCompletionResult, *, limit: int = 3) -> str:
        missing = "; ".join(result.missing[:limit])
        suffix = "" if len(result.missing) <= limit else f"; +{len(result.missing) - limit} more"
        return f"Completion rejected by task evidence: {missing}{suffix}"


class TaskEventWriter:
    """Typed append-only commands; every state change remains replayable."""

    def __init__(self, ledger: SessionLedger) -> None:
        self.ledger = ledger

    def create_contract(self, contract: TaskContract) -> SessionEvent:
        if TaskStateProjector().project(self.ledger.events).contract is not None:
            raise ValueError("task contract already exists")
        return self.ledger.append(TASK_CONTRACT_CREATED, {"contract": contract.to_payload()})

    def update_state(self, delta: Mapping[str, Any], *, reason: str) -> SessionEvent:
        if not delta:
            raise ValueError("state delta must not be empty")
        return self.ledger.append(STATE_UPDATED, {"delta": dict(delta), "reason": reason})

    def add_evidence(self, evidence: Evidence) -> SessionEvent:
        state = TaskStateProjector().project(self.ledger.events)
        if any(item.id == evidence.id for item in state.evidence):
            raise ValueError(f"evidence id already exists: {evidence.id!r}")
        return self.ledger.append(EVIDENCE_ADDED, {"evidence": evidence.to_payload()})

    def classify_failure(self, failure: Failure) -> SessionEvent:
        state = TaskStateProjector().project(self.ledger.events)
        if any(item.id == failure.id for item in state.failures):
            raise ValueError(f"failure id already exists: {failure.id!r}")
        return self.ledger.append(FAILURE_CLASSIFIED, {"failure": failure.to_payload()})

    def check_completion(
        self,
        checker: RuleBasedContractChecker | None = None,
    ) -> ContractCompletionResult:
        state = TaskStateProjector().project(self.ledger.events)
        result = (checker or RuleBasedContractChecker()).check(state)
        self.ledger.append(COMPLETION_CHECKED, result.to_payload())
        return result


def evidence_from_tool_result(result: ToolResult) -> tuple[Evidence, ...]:
    """Accept only explicit structured evidence; never infer success from prose."""

    raw_evidence = result.metadata.get("evidence")
    if raw_evidence is None:
        return ()
    if not isinstance(raw_evidence, list):
        raise ValueError("ToolResult metadata.evidence must be a list")
    evidence: list[Evidence] = []
    for index, raw in enumerate(raw_evidence):
        if not isinstance(raw, Mapping):
            raise ValueError("ToolResult evidence entries must be objects")
        kind = EvidenceKind(raw["kind"])
        source = (
            EvidenceSource.ARTIFACT_INSPECTION
            if kind is EvidenceKind.ARTIFACT
            else EvidenceSource.TOOL_RESULT
        )
        evidence.append(
            Evidence(
                id=str(raw.get("id") or f"{result.call_id}:evidence:{index + 1}"),
                kind=kind,
                subject=str(raw["subject"]),
                value=raw.get("value"),
                source=source,
                metadata=dict(raw.get("metadata") or {}),
            )
        )
    return tuple(evidence)


def _normalize_subject(value: str) -> str:
    normalized = " ".join(value.strip().lower().replace("\\", "/").split())
    return normalized.removeprefix("/task/")


def _compact_text(value: str, limit: int = 500) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…<{len(value) - limit} chars omitted>"


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_text(value)
    if isinstance(value, Mapping):
        return {str(key): _compact_value(item) for key, item in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        return [_compact_value(item) for item in value[:20]]
    return value
