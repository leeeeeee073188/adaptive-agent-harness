"""Governed offline evolution of immutable Harness profile versions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from adaptive_harness.config import Profile
from adaptive_harness.ledger import SessionEvent, SessionLedger

BASELINE_REGISTERED = "evolution/baseline-registered"
CANDIDATE_CREATED = "evolution/candidate-created"
SHADOW_EVALUATED = "evolution/shadow-evaluated"
VERSION_PROMOTED = "evolution/promoted"
VERSION_REJECTED = "evolution/rejected"
VERSION_ROLLED_BACK = "evolution/rolled-back"


class VersionStatus(StrEnum):
    SHADOW = "shadow"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class EvolutionDisposition(StrEnum):
    KEEP_SHADOW = "keep_shadow"
    PROMOTE = "promote"
    REJECT = "reject"


@dataclass(frozen=True)
class EvolutionCandidate:
    version_id: str
    base_version_id: str
    profile_fingerprint: str
    hypothesis: str
    component_changes: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_profile(
        cls,
        *,
        version_id: str,
        base_version_id: str,
        profile: Profile,
        hypothesis: str,
        component_changes: Iterable[str],
        evidence_refs: Iterable[str],
    ) -> EvolutionCandidate:
        return cls(
            version_id=version_id,
            base_version_id=base_version_id,
            profile_fingerprint=profile.fingerprint(),
            hypothesis=hypothesis,
            component_changes=tuple(component_changes),
            evidence_refs=tuple(evidence_refs),
        )

    def validate(self) -> None:
        if not self.version_id.strip() or not self.base_version_id.strip():
            raise ValueError("candidate version ids must be non-empty")
        if not self.hypothesis.strip():
            raise ValueError("candidate hypothesis must be non-empty")
        if not self.component_changes:
            raise ValueError("candidate must name at least one component change")
        if not self.evidence_refs:
            raise ValueError("candidate must cite provenance evidence")
        if len(self.profile_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.profile_fingerprint.lower()
        ):
            raise ValueError("candidate profile fingerprint must be sha256")

    def to_payload(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "base_version_id": self.base_version_id,
            "profile_fingerprint": self.profile_fingerprint,
            "hypothesis": self.hypothesis,
            "component_changes": list(self.component_changes),
            "evidence_refs": list(self.evidence_refs),
            "execution_scope": "shadow-only",
        }


@dataclass(frozen=True)
class ShadowEvaluation:
    version_id: str
    matched_samples: int
    quality_delta: float | None
    quality_delta_lower_bound: float | None
    token_delta_ratio: float | None
    regressions: int
    integrity_passed: bool
    leakage_passed: bool
    evaluation_ref: str

    def validate(self) -> None:
        if not self.version_id.strip() or not self.evaluation_ref.strip():
            raise ValueError("evaluation version_id and evaluation_ref must be non-empty")
        if self.matched_samples < 0 or self.regressions < 0:
            raise ValueError("evaluation counts must be non-negative")

    def to_payload(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "matched_samples": self.matched_samples,
            "quality_delta": self.quality_delta,
            "quality_delta_lower_bound": self.quality_delta_lower_bound,
            "token_delta_ratio": self.token_delta_ratio,
            "regressions": self.regressions,
            "integrity_passed": self.integrity_passed,
            "leakage_passed": self.leakage_passed,
            "evaluation_ref": self.evaluation_ref,
        }


@dataclass(frozen=True)
class EvolutionGateConfig:
    min_matched_samples: int = 5
    min_quality_delta_lower_bound: float = 0.0
    max_token_delta_ratio: float = 0.10
    max_regressions: int = 0

    def __post_init__(self) -> None:
        if self.min_matched_samples < 1:
            raise ValueError("min_matched_samples must be positive")
        if self.max_token_delta_ratio < 0 or self.max_regressions < 0:
            raise ValueError("cost and regression limits must be non-negative")


@dataclass(frozen=True)
class EvolutionDecision:
    disposition: EvolutionDisposition
    reasons: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {"disposition": self.disposition.value, "reasons": list(self.reasons)}


class EvolutionGate:
    """Pure fail-closed promotion policy over paired Shadow evidence."""

    def __init__(self, config: EvolutionGateConfig | None = None) -> None:
        self.config = config or EvolutionGateConfig()

    def decide(self, evaluation: ShadowEvaluation) -> EvolutionDecision:
        evaluation.validate()
        hard_failures = []
        if not evaluation.integrity_passed:
            hard_failures.append("evaluation integrity failed")
        if not evaluation.leakage_passed:
            hard_failures.append("experience leakage check failed")
        if evaluation.regressions > self.config.max_regressions:
            hard_failures.append(
                f"regressions {evaluation.regressions} exceed {self.config.max_regressions}"
            )
        if (
            evaluation.quality_delta_lower_bound is not None
            and evaluation.quality_delta_lower_bound < self.config.min_quality_delta_lower_bound
        ):
            hard_failures.append(
                "quality confidence lower bound is below the promotion threshold"
            )
        if (
            evaluation.token_delta_ratio is not None
            and evaluation.token_delta_ratio > self.config.max_token_delta_ratio
        ):
            hard_failures.append("token cost increase exceeds the promotion threshold")
        if hard_failures:
            return EvolutionDecision(EvolutionDisposition.REJECT, tuple(hard_failures))

        incomplete = []
        if evaluation.matched_samples < self.config.min_matched_samples:
            incomplete.append(
                f"matched samples {evaluation.matched_samples} below {self.config.min_matched_samples}"
            )
        if evaluation.quality_delta is None or evaluation.quality_delta_lower_bound is None:
            incomplete.append("quality effect is not estimable")
        if evaluation.token_delta_ratio is None:
            incomplete.append("token cost effect is not estimable")
        if incomplete:
            return EvolutionDecision(EvolutionDisposition.KEEP_SHADOW, tuple(incomplete))
        return EvolutionDecision(EvolutionDisposition.PROMOTE, ("all promotion gates passed",))


@dataclass(frozen=True)
class ProfileVersionState:
    version_id: str
    profile_fingerprint: str
    parent_version_id: str | None
    hypothesis: str
    status: VersionStatus


@dataclass(frozen=True)
class EvolutionState:
    versions: Mapping[str, ProfileVersionState]
    active_version_id: str | None
    decisions: Mapping[str, EvolutionDecision]


class EvolutionProjector:
    def project(self, events: Iterable[SessionEvent]) -> EvolutionState:
        versions: dict[str, ProfileVersionState] = {}
        decisions: dict[str, EvolutionDecision] = {}
        active: str | None = None
        for event in events:
            payload = event.payload
            if event.type == BASELINE_REGISTERED:
                version_id = str(payload["version_id"])
                versions[version_id] = ProfileVersionState(
                    version_id,
                    str(payload["profile_fingerprint"]),
                    None,
                    "initial baseline",
                    VersionStatus.PROMOTED,
                )
                active = version_id
            elif event.type == CANDIDATE_CREATED:
                version_id = str(payload["version_id"])
                versions[version_id] = ProfileVersionState(
                    version_id,
                    str(payload["profile_fingerprint"]),
                    str(payload["base_version_id"]),
                    str(payload["hypothesis"]),
                    VersionStatus.SHADOW,
                )
            elif event.type == SHADOW_EVALUATED:
                version_id = str(payload["version_id"])
                raw_decision = payload["decision"]
                decisions[version_id] = EvolutionDecision(
                    EvolutionDisposition(str(raw_decision["disposition"])),
                    tuple(str(reason) for reason in raw_decision["reasons"]),
                )
            elif event.type in {VERSION_PROMOTED, VERSION_REJECTED}:
                version_id = str(payload["version_id"])
                status = (
                    VersionStatus.PROMOTED
                    if event.type == VERSION_PROMOTED
                    else VersionStatus.REJECTED
                )
                versions[version_id] = replace(versions[version_id], status=status)
                raw_decision = payload["decision"]
                decisions[version_id] = EvolutionDecision(
                    EvolutionDisposition(str(raw_decision["disposition"])),
                    tuple(str(reason) for reason in raw_decision["reasons"]),
                )
                if status is VersionStatus.PROMOTED:
                    active = version_id
            elif event.type == VERSION_ROLLED_BACK:
                from_id = str(payload["from_version_id"])
                to_id = str(payload["to_version_id"])
                versions[from_id] = replace(versions[from_id], status=VersionStatus.ROLLED_BACK)
                versions[to_id] = replace(versions[to_id], status=VersionStatus.PROMOTED)
                active = to_id
        return EvolutionState(dict(versions), active, dict(decisions))


class EvolutionManager:
    """Append-only lifecycle manager; it never mutates a running Profile."""

    def __init__(self, ledger: SessionLedger, gate: EvolutionGate | None = None) -> None:
        self.ledger = ledger
        self.gate = gate or EvolutionGate()
        self.projector = EvolutionProjector()

    @property
    def state(self) -> EvolutionState:
        return self.projector.project(self.ledger.events)

    def register_baseline(self, version_id: str, profile: Profile) -> None:
        state = self.state
        if state.versions:
            raise RuntimeError("baseline can only be registered in an empty evolution ledger")
        if not version_id.strip():
            raise ValueError("baseline version_id must be non-empty")
        self.ledger.append(
            BASELINE_REGISTERED,
            {"version_id": version_id, "profile_fingerprint": profile.fingerprint()},
        )

    def create_candidate(self, candidate: EvolutionCandidate) -> None:
        candidate.validate()
        state = self.state
        if candidate.version_id in state.versions:
            raise ValueError(f"profile version already exists: {candidate.version_id}")
        if candidate.base_version_id != state.active_version_id:
            raise ValueError("candidate must fork from the active profile version")
        base = state.versions[candidate.base_version_id]
        if candidate.profile_fingerprint == base.profile_fingerprint:
            raise ValueError("candidate profile must differ from its base")
        self.ledger.append(CANDIDATE_CREATED, candidate.to_payload())

    def evaluate(self, evaluation: ShadowEvaluation) -> EvolutionDecision:
        evaluation.validate()
        state = self.state
        version = state.versions.get(evaluation.version_id)
        if version is None:
            raise KeyError(f"unknown candidate version: {evaluation.version_id}")
        if version.status is not VersionStatus.SHADOW:
            raise RuntimeError("only a shadow candidate can be evaluated")
        decision = self.gate.decide(evaluation)
        self.ledger.append(
            SHADOW_EVALUATED,
            {**evaluation.to_payload(), "decision": decision.to_payload()},
        )
        if decision.disposition is EvolutionDisposition.PROMOTE:
            self.ledger.append(
                VERSION_PROMOTED,
                {
                    "version_id": evaluation.version_id,
                    "previous_version_id": state.active_version_id,
                    "decision": decision.to_payload(),
                },
            )
        elif decision.disposition is EvolutionDisposition.REJECT:
            self.ledger.append(
                VERSION_REJECTED,
                {"version_id": evaluation.version_id, "decision": decision.to_payload()},
            )
        return decision

    def rollback(self, to_version_id: str, *, reason: str) -> None:
        state = self.state
        if not reason.strip():
            raise ValueError("rollback reason must be non-empty")
        if state.active_version_id is None:
            raise RuntimeError("no active profile version to roll back")
        if to_version_id == state.active_version_id:
            raise ValueError("rollback target is already active")
        target = state.versions.get(to_version_id)
        if target is None or target.status is VersionStatus.REJECTED:
            raise ValueError("rollback target must be a known non-rejected version")
        ancestor_id = state.versions[state.active_version_id].parent_version_id
        ancestors = set()
        while ancestor_id is not None:
            ancestors.add(ancestor_id)
            ancestor_id = state.versions[ancestor_id].parent_version_id
        if to_version_id not in ancestors:
            raise ValueError("rollback target must be an ancestor of the active version")
        self.ledger.append(
            VERSION_ROLLED_BACK,
            {
                "from_version_id": state.active_version_id,
                "to_version_id": to_version_id,
                "reason": reason,
            },
        )
