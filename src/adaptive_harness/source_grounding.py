"""Replayable claim/source lineage and fail-closed synthesis admission.

The module stores only resource identities and content hashes. It does not
interpret task semantics, consume evaluator data, or trust assistant prose as
evidence. Integrations decide how public source handles are materialized.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}")


class SourceRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    PROVISIONAL = "provisional"
    REFERENCE = "reference"
    MODEL_PROSE = "model_prose"


class DerivationKind(StrEnum):
    DIRECT_COPY = "direct_copy"
    TRANSFORM = "transform"
    AGGREGATE = "aggregate"
    SYNTHESIS = "synthesis"


@dataclass(frozen=True)
class SourceHandle:
    id: str
    resource: str
    sha256: str
    role: SourceRole
    origin_event_seq: int | None = None
    final_eligible: bool = False

    def __post_init__(self) -> None:
        _require_id(self.id, "source id")
        _require_id(self.resource, "source resource")
        _require_hash(self.sha256)
        if self.origin_event_seq is not None and self.origin_event_seq < 0:
            raise ValueError("origin_event_seq must be non-negative")

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "resource": self.resource,
            "sha256": self.sha256,
            "role": self.role.value,
            "origin_event_seq": self.origin_event_seq,
            "final_eligible": self.final_eligible,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SourceHandle:
        return cls(
            str(payload["id"]),
            str(payload["resource"]),
            str(payload["sha256"]),
            SourceRole(str(payload["role"])),
            (
                int(payload["origin_event_seq"])
                if payload.get("origin_event_seq") is not None
                else None
            ),
            bool(payload.get("final_eligible", False)),
        )


@dataclass(frozen=True)
class ClaimAtom:
    id: str
    content_sha256: str
    supported_by: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_id(self.id, "claim id")
        _require_hash(self.content_sha256)
        if not self.supported_by:
            raise ValueError("claim support must not be empty")
        if any(not str(source_id).strip() for source_id in self.supported_by):
            raise ValueError("claim support ids must be non-empty")

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "content_sha256": self.content_sha256,
            "supported_by": list(self.supported_by),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ClaimAtom:
        return cls(
            str(payload["id"]),
            str(payload["content_sha256"]),
            tuple(str(item) for item in payload.get("supported_by") or ()),
        )


@dataclass(frozen=True)
class ArtifactDerivation:
    path: str
    sha256: str
    source_ids: tuple[str, ...]
    kind: DerivationKind
    claims: tuple[ClaimAtom, ...] = ()

    def __post_init__(self) -> None:
        _require_id(self.path, "artifact path")
        _require_hash(self.sha256)
        if not self.source_ids:
            raise ValueError("artifact derivation sources must not be empty")

    def to_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "source_ids": list(self.source_ids),
            "kind": self.kind.value,
            "claims": [claim.to_payload() for claim in self.claims],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ArtifactDerivation:
        return cls(
            str(payload["path"]),
            str(payload["sha256"]),
            tuple(str(item) for item in payload.get("source_ids") or ()),
            DerivationKind(str(payload["kind"])),
            tuple(ClaimAtom.from_payload(item) for item in payload.get("claims") or ()),
        )


@dataclass(frozen=True)
class LineageGraph:
    sources: tuple[SourceHandle, ...]
    artifact: ArtifactDerivation

    def to_payload(self) -> dict[str, object]:
        return {
            "sources": [source.to_payload() for source in self.sources],
            "artifact": self.artifact.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> LineageGraph:
        raw_sources = payload.get("sources") or ()
        if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
            raise ValueError("lineage sources must be a list")
        raw_artifact = payload.get("artifact")
        if not isinstance(raw_artifact, Mapping):
            raise ValueError("lineage artifact must be an object")
        return cls(
            tuple(SourceHandle.from_payload(item) for item in raw_sources),
            ArtifactDerivation.from_payload(raw_artifact),
        )


@dataclass(frozen=True)
class GroundingRequirements:
    min_authoritative_sources: int = 1
    forbid_model_prose: bool = True
    forbid_exact_provisional_copy: bool = True

    def __post_init__(self) -> None:
        if self.min_authoritative_sources < 0:
            raise ValueError("min_authoritative_sources must be non-negative")


@dataclass(frozen=True)
class GroundingDecision:
    passed: bool
    reasons: tuple[str, ...]
    authoritative_source_count: int
    missing_source_ids: tuple[str, ...] = ()
    forbidden_source_ids: tuple[str, ...] = ()
    exact_provisional_copies: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "authoritative_source_count": self.authoritative_source_count,
            "missing_source_ids": list(self.missing_source_ids),
            "forbidden_source_ids": list(self.forbidden_source_ids),
            "exact_provisional_copies": list(self.exact_provisional_copies),
        }


class SourceGroundingGate:
    """Admit an artifact only when its declared public lineage is coherent."""

    def evaluate(
        self,
        graph: LineageGraph,
        requirements: GroundingRequirements,
    ) -> GroundingDecision:
        sources: dict[str, SourceHandle] = {}
        duplicate_ids: set[str] = set()
        for source in graph.sources:
            if source.id in sources:
                duplicate_ids.add(source.id)
            sources[source.id] = source

        referenced = set(graph.artifact.source_ids)
        referenced.update(
            source_id
            for claim in graph.artifact.claims
            for source_id in claim.supported_by
        )
        missing = tuple(sorted(referenced - sources.keys()))
        forbidden = tuple(
            sorted(
                source_id
                for source_id in referenced & sources.keys()
                if requirements.forbid_model_prose
                and sources[source_id].role is SourceRole.MODEL_PROSE
            )
        )
        authoritative = {
            source_id
            for source_id in referenced & sources.keys()
            if sources[source_id].role is SourceRole.AUTHORITATIVE
        }
        exact_copies = tuple(
            sorted(
                source.id
                for source in graph.sources
                if requirements.forbid_exact_provisional_copy
                and source.role is SourceRole.PROVISIONAL
                and source.sha256 == graph.artifact.sha256
            )
        )
        illegal_direct_copy = tuple(
            sorted(
                source_id
                for source_id in graph.artifact.source_ids
                if graph.artifact.kind is DerivationKind.DIRECT_COPY
                and source_id in sources
                and not sources[source_id].final_eligible
            )
        )

        reasons: list[str] = []
        if duplicate_ids:
            reasons.append("Duplicate source ids: " + ", ".join(sorted(duplicate_ids)))
        if missing:
            reasons.append("Missing source handles: " + ", ".join(missing))
        if forbidden:
            reasons.append("Model prose cannot support grounded claims: " + ", ".join(forbidden))
        if len(authoritative) < requirements.min_authoritative_sources:
            reasons.append(
                "Authoritative source coverage below minimum: "
                f"{len(authoritative)}/{requirements.min_authoritative_sources}"
            )
        if exact_copies:
            reasons.append(
                "Artifact exactly copies provisional sources: " + ", ".join(exact_copies)
            )
        if illegal_direct_copy:
            reasons.append(
                "Direct-copy sources are not final-eligible: "
                + ", ".join(illegal_direct_copy)
            )
        return GroundingDecision(
            not reasons,
            tuple(reasons) if reasons else ("Public source lineage satisfies grounding requirements.",),
            len(authoritative),
            missing,
            forbidden,
            exact_copies,
        )


def _require_id(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _require_hash(value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError("content hashes must be lowercase SHA-256 hex")
