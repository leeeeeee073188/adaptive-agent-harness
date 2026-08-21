"""Observation providers for RealReplicaBench public mock interfaces."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adaptive_harness.integrations.loopback_mcp import call_mcp_tool, validate_loopback_endpoint
from adaptive_harness.task_contract import CriterionKind, TaskContract
from adaptive_harness.task_state import Evidence, EvidenceKind, EvidenceSource

if TYPE_CHECKING:
    from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary


class WorkbenchCalendarObservationProvider:
    """Read the benchmark workbench's public materialized created-event state."""

    def __init__(self, task_root: Path) -> None:
        self.state_path = (task_root.resolve() / "outputs/mock_state/workbench_final.json").resolve()

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject") == "calendar.event_created"
            and bool(criterion.parameters.get("target_any"))
        )

    def observe(
        self,
        contract: TaskContract,
        summary: DeerFlowReplaySummary,
        *,
        turn: int,
    ) -> Sequence[Evidence]:
        document = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
        created = document.get("created_events") if isinstance(document, Mapping) else []
        evidence = []
        for criterion in contract.criteria:
            if not self.supports(criterion):
                continue
            targets = [str(item).lower() for item in criterion.parameters["target_any"]]
            matched = any(
                isinstance(event, Mapping)
                and any(target in str(event.get("title") or "").lower() for target in targets)
                for event in created or ()
            )
            evidence.append(
                Evidence(
                    f"deerflow:t{turn}:workbench:calendar.event_created",
                    EvidenceKind.OBSERVATION,
                    "calendar.event_created",
                    matched,
                    EvidenceSource.RUNTIME_OBSERVATION,
                    {
                        "provider": "workbench-materialized-state",
                        "target_any": list(criterion.parameters["target_any"]),
                    },
                )
            )
        return tuple(evidence)


class GmailMcpObservationProvider:
    """Verify public label/event postconditions through read-only MCP tools."""

    def __init__(self, endpoint: str, *, call_tool: Callable[..., Any] | None = None) -> None:
        validate_loopback_endpoint(endpoint)
        self.endpoint = endpoint
        self.call_tool = call_tool or call_mcp_tool

    def supports(self, criterion: Any) -> bool:
        return (
            criterion.kind is CriterionKind.OBSERVATION_EQUALS
            and criterion.parameters.get("subject") in {"mail.label_created", "calendar.event_created"}
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
        validate_loopback_endpoint(endpoint)
        self.endpoint = endpoint
        self.call_tool = call_tool or call_mcp_tool
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

