"""Public-client policy bridge from DeerFlow turns into Harness task facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import parse_qsl, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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
from adaptive_harness.redaction import normalize_key, render_redacted, scrub_tool_result_text
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

    def before_run(self, contract: TaskContract) -> Sequence[Evidence] | None: ...

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]: ...


class PublicSourceAccessObservationProvider:
    """Materialize explicit public loopback source-access obligations before model turns.

    This provider is intentionally narrow: it only proves task-prompt
    ``source.access:*`` criteria for loopback HTTP resources that are already
    visible to the model. It never fetches external hosts and never treats a
    failed snapshot as success.
    """

    MAX_BYTES = 64 * 1024
    EXCERPT_CHARS = 4000

    def __init__(
        self,
        *,
        fetch_bytes: Callable[[str], tuple[str, bytes]] | None = None,
    ) -> None:
        self.fetch_bytes = fetch_bytes or _fetch_loopback_bytes

    def supports(self, criterion: Any) -> bool:
        return _is_loopback_source_access_criterion(criterion)

    def before_run(self, contract: TaskContract) -> Sequence[Evidence]:
        evidence: list[Evidence] = []
        for criterion in contract.criteria:
            if not self.supports(criterion):
                continue
            resource = str(criterion.parameters["resource"])
            final_url, body = self.fetch_bytes(resource)
            if not _is_safe_loopback_http_url(final_url):
                raise ValueError(f"source access snapshot redirected outside loopback: {final_url}")
            if len(body) > self.MAX_BYTES:
                raise ValueError(
                    f"source access snapshot for {resource} exceeds {self.MAX_BYTES} bytes"
                )
            rendered = _render_public_payload_excerpt(body)
            evidence.append(
                Evidence(
                    id=f"source-snapshot:{criterion.id}",
                    kind=EvidenceKind.OBSERVATION,
                    subject=str(criterion.parameters["subject"]),
                    value=True,
                    source=EvidenceSource.RUNTIME_OBSERVATION,
                    metadata={
                        "resource": resource,
                        "hash": hashlib.sha256(body).hexdigest(),
                        "chars": len(rendered),
                        "public_payload_excerpt": rendered[: self.EXCERPT_CHARS],
                    },
                )
            )
        return tuple(evidence)

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        return ()


class FileArtifactObservationProvider:
    """Verify public artifact criteria against an isolated task root."""

    MAX_GROUNDING_FILES = 512
    MAX_GROUNDING_FILE_BYTES = 2 * 1024 * 1024
    MAX_ARTIFACT_DIRECTORY_FILES = 512

    def __init__(self, task_root: Path) -> None:
        self.task_root = task_root.resolve()

    def supports(self, criterion: Any) -> bool:
        return criterion.kind in {CriterionKind.ARTIFACT_EXISTS, CriterionKind.OBSERVATION_EQUALS} and (
            criterion.kind is CriterionKind.ARTIFACT_EXISTS
            or str(criterion.parameters.get("subject") or "").startswith(
                ("artifact.json_shape:", "artifact.grounding:")
            )
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
            if criterion.kind is CriterionKind.ARTIFACT_EXISTS:
                relative = str(criterion.parameters["path"]).removeprefix("/task/")
                path = self._artifact_path(relative)
                is_file = path.is_file()
                is_directory = path.is_dir()
                exists = is_file or is_directory
                directory_inventory = (
                    self._directory_inventory(path) if is_directory else None
                )
                value = {
                    "exists": exists,
                    "kind": "file" if is_file else "directory" if is_directory else None,
                    "size": path.stat().st_size if is_file else None,
                    "sha256": _file_sha256(path) if is_file else None,
                    "visible_file_count": (
                        directory_inventory["visible_file_count"]
                        if directory_inventory is not None
                        else None
                    ),
                    "visible_file_count_truncated": (
                        directory_inventory["truncated"]
                        if directory_inventory is not None
                        else None
                    ),
                    "skipped_entry_count": (
                        directory_inventory["skipped_entry_count"]
                        if directory_inventory is not None
                        else None
                    ),
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
                continue
            if not self.supports(criterion):
                continue
            relative = str(criterion.parameters["path"]).removeprefix("/task/")
            path = self._artifact_path(relative)
            subject = str(criterion.parameters["subject"])
            if subject.startswith("artifact.grounding:"):
                grounding = self._grounding_diagnostics(path, criterion.parameters)
                evidence.append(
                    Evidence(
                        id=f"deerflow:t{turn}:{criterion.id}",
                        kind=EvidenceKind.OBSERVATION,
                        subject=subject,
                        value=not grounding["diagnostics"],
                        source=EvidenceSource.ARTIFACT_INSPECTION,
                        metadata={
                            "provider": "filesystem-provisional-copy-guard",
                            "path": relative,
                            **grounding,
                        },
                    )
                )
                continue
            diagnostics = self._json_shape_diagnostics(path, criterion.parameters)
            evidence.append(
                Evidence(
                    id=f"deerflow:t{turn}:{criterion.id}",
                    kind=EvidenceKind.OBSERVATION,
                    subject=str(criterion.parameters["subject"]),
                    value=not diagnostics,
                    source=EvidenceSource.ARTIFACT_INSPECTION,
                    metadata={
                        "provider": "filesystem-json-shape",
                        "path": relative,
                        "diagnostics": diagnostics,
                    },
                )
            )
        return tuple(evidence)

    def _directory_inventory(self, path: Path) -> dict[str, int | bool]:
        count = 0
        skipped = 0
        truncated = False
        for candidate in path.rglob("*"):
            try:
                relative = candidate.relative_to(path)
                if candidate.is_symlink() or any(
                    part.startswith(".") for part in relative.parts
                ):
                    skipped += 1
                    continue
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(path) or not resolved.is_file():
                    continue
            except OSError:
                skipped += 1
                continue
            if count >= self.MAX_ARTIFACT_DIRECTORY_FILES:
                truncated = True
                break
            count += 1
        return {
            "visible_file_count": count,
            "truncated": truncated,
            "skipped_entry_count": skipped,
        }

    def _artifact_path(self, relative: str) -> Path:
        path = (self.task_root / relative).resolve()
        if not path.is_relative_to(self.task_root):
            raise ValueError(f"artifact criterion escapes task root: {relative}")
        return path

    def _json_shape_diagnostics(self, path: Path, parameters: Mapping[str, Any]) -> list[str]:
        if not path.is_file():
            return ["artifact missing"]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return [f"invalid json: {error}"]
        shape = parameters.get("shape")
        if not isinstance(shape, Mapping):
            return ["invalid shape criterion parameters"]
        diagnostics: list[str] = []
        _validate_json_shape(payload, shape, "$", diagnostics)
        identity_keys = parameters.get("list_identity_keys") or {}
        if isinstance(identity_keys, Mapping):
            _validate_json_identity_keys(payload, identity_keys, diagnostics)
        non_vacuous_paths = parameters.get("non_vacuous_collection_paths") or ()
        if isinstance(non_vacuous_paths, Sequence) and not isinstance(
            non_vacuous_paths, (str, bytes)
        ):
            _validate_json_collection_non_vacuity(payload, non_vacuous_paths, diagnostics)
        return diagnostics

    def _grounding_diagnostics(
        self,
        path: Path,
        parameters: Mapping[str, Any],
    ) -> dict[str, list[str]]:
        diagnostics: list[str] = []
        exact_copies: list[str] = []
        checked: list[str] = []
        missing: list[str] = []
        skipped: list[str] = []
        if not path.is_file():
            diagnostics.append("artifact missing")
            return {
                "diagnostics": diagnostics,
                "exact_provisional_copies": exact_copies,
                "checked_provisional_sources": checked,
                "missing_provisional_sources": missing,
                "skipped_provisional_sources": skipped,
            }
        artifact_hash = _file_sha256(path)
        raw_paths = parameters.get("provisional_source_paths") or ()
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            diagnostics.append("invalid provisional source parameters")
        else:
            provisional_paths = [str(raw).removeprefix("/task/") for raw in raw_paths]
            if parameters.get("scan_public_workspace") is True:
                workspace = self._artifact_path("workspace")
                if workspace.is_dir():
                    discovered_count = 0
                    for candidate in workspace.rglob("*"):
                        relative_to_workspace = candidate.relative_to(workspace)
                        if (
                            not candidate.is_file()
                            or candidate.is_symlink()
                            or any(part.startswith(".") for part in relative_to_workspace.parts)
                        ):
                            continue
                        if discovered_count >= self.MAX_GROUNDING_FILES:
                            skipped.append("<workspace scan truncated>")
                            if parameters.get("forbid_exact_copy") is True:
                                diagnostics.append(
                                    "workspace provisional scan truncated at "
                                    f"{self.MAX_GROUNDING_FILES} files"
                                )
                            break
                        provisional_paths.append(
                            candidate.relative_to(self.task_root).as_posix()
                        )
                        discovered_count += 1
            for relative in dict.fromkeys(provisional_paths):
                source_path = self._artifact_path(relative)
                if not source_path.is_file():
                    missing.append(relative)
                    continue
                try:
                    size = source_path.stat().st_size
                except OSError as error:
                    skipped.append(relative)
                    if parameters.get("forbid_exact_copy") is True:
                        diagnostics.append(
                            f"provisional source could not be verified: {relative}: {error}"
                        )
                    continue
                if size > self.MAX_GROUNDING_FILE_BYTES:
                    skipped.append(relative)
                    if parameters.get("forbid_exact_copy") is True:
                        diagnostics.append(
                            "provisional source not verified due to size limit: " + relative
                        )
                    continue
                checked.append(relative)
                try:
                    source_hash = _file_sha256(source_path)
                except OSError as error:
                    checked.pop()
                    skipped.append(relative)
                    if parameters.get("forbid_exact_copy") is True:
                        diagnostics.append(
                            f"provisional source could not be verified: {relative}: {error}"
                        )
                    continue
                if source_hash == artifact_hash:
                    exact_copies.append(relative)
            if parameters.get("forbid_exact_copy") is True and exact_copies:
                diagnostics.append(
                    "artifact exactly copies provisional source requiring validation: "
                    + ", ".join(exact_copies)
                )
        return {
            "diagnostics": diagnostics,
            "exact_provisional_copies": exact_copies,
            "checked_provisional_sources": checked,
            "missing_provisional_sources": missing,
            "skipped_provisional_sources": skipped,
        }


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
                for evidence in before_run(contract) or ():
                    writer.add_evidence(evidence)
            except (KeyError, OSError, TypeError, ValueError) as error:
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
            except OSError as error:
                writer.classify_failure(
                    Failure(
                        id=f"deerflow:t{turn}:observation-provider:{provider_index + 1}",
                        error_type="OBSERVATION_SNAPSHOT_FAILED",
                        message=str(error),
                        source=EvidenceSource.RUNTIME_OBSERVATION,
                        metadata={"provider": type(provider).__name__},
                    )
                )
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
        if (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and str(criterion.parameters.get("subject") or "").startswith("source.access")
            and str(criterion.parameters.get("resource") or "").startswith(("http://", "https://"))
        ):
            return True
        return any(
            bool(getattr(provider, "supports", lambda _criterion: False)(criterion))
            for provider in self.observation_providers
        )


def _is_loopback_source_access_criterion(criterion: Any) -> bool:
    parameters = getattr(criterion, "parameters", {})
    if not isinstance(parameters, Mapping):
        return False
    return (
        getattr(criterion, "kind", None) is CriterionKind.OBSERVATION_EQUALS
        and bool(getattr(criterion, "required", False))
        and str(parameters.get("subject") or "").startswith("source.access:")
        and parameters.get("expected") is True
        and _is_safe_loopback_http_url(str(parameters.get("resource") or ""))
    )


def _is_safe_loopback_http_url(raw: str) -> bool:
    parsed = urlparse(raw)
    if parsed.scheme.lower() != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = normalize_key(key)
        if normalized in {
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "client_secret",
            "code",
            "credential",
            "password",
            "refresh_token",
            "secret",
            "signature",
            "token",
            "access_token",
        } or normalized.endswith(("_token", "_secret")):
            return False
    return True


def _fetch_loopback_bytes(url: str) -> tuple[str, bytes]:
    if not _is_safe_loopback_http_url(url):
        raise ValueError(f"unsafe source access snapshot URL: {url}")
    opener = build_opener(_LoopbackRedirectHandler)
    request = Request(url, headers={"Accept": "application/json,text/plain,*/*"})
    with opener.open(request, timeout=5) as response:
        final_url = response.geturl()
        if not _is_safe_loopback_http_url(final_url):
            raise ValueError(f"source access snapshot redirected outside loopback: {final_url}")
        body = response.read(PublicSourceAccessObservationProvider.MAX_BYTES + 1)
    return final_url, body


class _LoopbackRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not _is_safe_loopback_http_url(str(newurl)):
            raise ValueError(f"source access snapshot redirected outside loopback: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _render_public_payload_excerpt(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return scrub_tool_result_text(text)
    return render_redacted(payload)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _validate_json_shape(
    value: Any,
    shape: Mapping[str, Any],
    location: str,
    diagnostics: list[str],
) -> None:
    expected_type = str(shape.get("type") or "any")
    if expected_type != "any" and not _json_type_matches(value, expected_type):
        diagnostics.append(
            f"wrong type {location}: expected {expected_type}, observed {_json_type_name(value)}"
        )
        return
    if expected_type == "object":
        if not isinstance(value, Mapping):
            return
        for key in shape.get("required") or ():
            if key not in value:
                diagnostics.append(f"missing required key {location}.{key}")
        properties = shape.get("properties")
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                if key in value and isinstance(child, Mapping):
                    _validate_json_shape(value[key], child, f"{location}.{key}", diagnostics)
    if expected_type == "array":
        if not isinstance(value, list):
            return
        item_shape = shape.get("items")
        if isinstance(item_shape, Mapping):
            for index, item in enumerate(value):
                _validate_json_shape(item, item_shape, f"{location}[{index}]", diagnostics)


def _json_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (isinstance(value, int | float) and not isinstance(value, bool))
    if expected_type == "null":
        return value is None
    return True


def _json_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def _validate_json_identity_keys(
    payload: Any,
    identity_keys: Mapping[str, Any],
    diagnostics: list[str],
) -> None:
    for path, raw_keys in identity_keys.items():
        keys = [str(item) for item in raw_keys] if isinstance(raw_keys, list) else [str(raw_keys)]
        if not keys:
            continue
        values = _values_at_json_path(payload, str(path))
        for value in values:
            if not isinstance(value, list):
                continue
            seen: set[tuple[str, ...]] = set()
            for item in value:
                if not isinstance(item, Mapping) or any(key not in item for key in keys):
                    continue
                identity = tuple(str(item[key]) for key in keys if _is_scalar_identity(item[key]))
                if len(identity) != len(keys):
                    continue
                if identity in seen:
                    key_label = ",".join(keys)
                    diagnostics.append(f"duplicate identity {path} ({key_label})={identity!r}")
                seen.add(identity)


def _validate_json_collection_non_vacuity(
    payload: Any,
    raw_paths: Sequence[Any],
    diagnostics: list[str],
) -> None:
    paths = tuple(dict.fromkeys(str(path) for path in raw_paths if str(path).startswith("$.")))
    if not paths:
        return
    collections = [
        value
        for path in paths
        for value in _values_at_json_path(payload, path)
        if isinstance(value, list)
    ]
    if collections and all(not value for value in collections):
        diagnostics.append(
            "all completeness-scoped collections are empty: " + ", ".join(paths)
        )


def _is_scalar_identity(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) and value is not None


def _values_at_json_path(payload: Any, path: str) -> list[Any]:
    if path == "$":
        return [payload]
    if not path.startswith("$."):
        return []
    current = [payload]
    for part in path[2:].split("."):
        next_values: list[Any] = []
        array = part.endswith("[]")
        key = part[:-2] if array else part
        for value in current:
            if not isinstance(value, Mapping) or key not in value:
                continue
            child = value[key]
            if array and isinstance(child, list):
                next_values.extend(child)
            elif not array:
                next_values.append(child)
        current = next_values
    return current
