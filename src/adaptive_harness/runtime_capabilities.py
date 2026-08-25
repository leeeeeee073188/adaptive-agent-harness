"""Runtime-neutral capability attestations and deterministic backend selection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from adaptive_harness.runtime_contract import reject_runtime_credentials


class RuntimeCapability(StrEnum):
    TOOL_CALLING = "tool_calling"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    SHELL = "shell"
    BROWSER = "browser"
    VISION_INPUT = "vision_input"
    SANDBOX = "sandbox"
    MULTI_TURN = "multi_turn"
    FINAL_RESPONSE = "final_response"
    ARTIFACT_FILE = "artifact_file"
    ARTIFACT_DIRECTORY = "artifact_directory"
    BEFORE_RUN_EVIDENCE = "before_run_evidence"
    STRUCTURED_OBSERVATION = "structured_observation"
    DURABLE_LEDGER = "durable_ledger"


class CapabilityAssurance(StrEnum):
    UNAVAILABLE = "unavailable"
    CLAIMED = "claimed"
    VERIFIED = "verified"


_ASSURANCE_RANK = {
    CapabilityAssurance.UNAVAILABLE: 0,
    CapabilityAssurance.CLAIMED: 1,
    CapabilityAssurance.VERIFIED: 2,
}
_EVIDENCE_SCHEMES = ("evidence://", "docs://", "runtime://", "test://")
_VERIFIED_EVIDENCE_SCHEMES = ("evidence://", "test://")


@dataclass(frozen=True)
class CapabilityAttestation:
    capability: RuntimeCapability
    assurance: CapabilityAssurance
    evidence_ref: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "limitations", tuple(self.limitations))
        if not isinstance(self.capability, RuntimeCapability) or not isinstance(
            self.assurance, CapabilityAssurance
        ):
            raise TypeError("capability attestation types are invalid")
        if not all(isinstance(item, str) for item in self.limitations):
            raise TypeError("capability limitations must be strings")
        if self.evidence_ref is not None and not isinstance(self.evidence_ref, str):
            raise TypeError("capability evidence_ref must be a string")
        if self.assurance is CapabilityAssurance.VERIFIED and not self.evidence_ref:
            raise ValueError("verified capability requires evidence_ref")
        if self.evidence_ref is not None and not self.evidence_ref.startswith(
            _EVIDENCE_SCHEMES
        ):
            raise ValueError("capability evidence_ref must use an approved public scheme")
        if self.assurance is CapabilityAssurance.VERIFIED and not str(
            self.evidence_ref
        ).startswith(_VERIFIED_EVIDENCE_SCHEMES):
            raise ValueError("verified capability requires material evidence")
        reject_runtime_credentials(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "capability": self.capability.value,
            "assurance": self.assurance.value,
            "evidence_ref": self.evidence_ref,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class RuntimeCapabilityProfile:
    runtime_id: str
    implementation_version: str
    attestations: tuple[CapabilityAttestation, ...]
    known_gaps: tuple[str, ...] = ()
    limits: tuple[tuple[str, int | float | str | bool], ...] = ()
    priority: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "attestations", tuple(self.attestations))
        object.__setattr__(self, "known_gaps", tuple(self.known_gaps))
        object.__setattr__(
            self,
            "limits",
            tuple((str(key), value) for key, value in self.limits),
        )
        if not isinstance(self.runtime_id, str) or not isinstance(
            self.implementation_version, str
        ):
            raise TypeError("runtime identity and version must be strings")
        if not self.runtime_id.strip() or not self.implementation_version.strip():
            raise ValueError("runtime identity and version must be non-empty")
        if not all(
            isinstance(item, CapabilityAttestation) for item in self.attestations
        ):
            raise TypeError("runtime attestations must be CapabilityAttestation values")
        if not all(isinstance(item, str) for item in self.known_gaps):
            raise TypeError("runtime known gaps must be strings")
        if not all(
            isinstance(value, (bool, int, float, str))
            for _key, value in self.limits
        ):
            raise TypeError("runtime limits must use scalar values")
        capabilities = [item.capability for item in self.attestations]
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("runtime capability attestations must be unique")
        limit_keys = [key for key, _value in self.limits]
        if len(limit_keys) != len(set(limit_keys)):
            raise ValueError("runtime limit keys must be unique")
        if not isinstance(self.priority, int) or self.priority < 0:
            raise ValueError("runtime priority must be non-negative")
        reject_runtime_credentials(self.to_payload())

    def assurance(self, capability: RuntimeCapability) -> CapabilityAssurance:
        return next(
            (
                item.assurance
                for item in self.attestations
                if item.capability is capability
            ),
            CapabilityAssurance.UNAVAILABLE,
        )

    def supports(self, requirement: RuntimeRequirement) -> bool:
        return _ASSURANCE_RANK[self.assurance(requirement.capability)] >= _ASSURANCE_RANK[
            requirement.minimum_assurance
        ]

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_payload(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "implementation_version": self.implementation_version,
            "attestations": [
                item.to_payload()
                for item in sorted(
                    self.attestations,
                    key=lambda attestation: attestation.capability.value,
                )
            ],
            "known_gaps": sorted(self.known_gaps),
            "limits": {key: value for key, value in sorted(self.limits)},
            "priority": self.priority,
        }


@dataclass(frozen=True)
class RuntimeRequirement:
    capability: RuntimeCapability
    minimum_assurance: CapabilityAssurance = CapabilityAssurance.VERIFIED

    def __post_init__(self) -> None:
        if not isinstance(self.capability, RuntimeCapability) or not isinstance(
            self.minimum_assurance, CapabilityAssurance
        ):
            raise TypeError("runtime requirement types are invalid")

    def to_payload(self) -> dict[str, str]:
        return {
            "capability": self.capability.value,
            "minimum_assurance": self.minimum_assurance.value,
        }


@dataclass(frozen=True)
class RuntimeSelectionRequest:
    required: tuple[RuntimeRequirement, ...]
    preferred: tuple[RuntimeCapability, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", tuple(self.required))
        object.__setattr__(self, "preferred", tuple(self.preferred))
        if not all(isinstance(item, RuntimeRequirement) for item in self.required):
            raise TypeError("runtime requirements must be RuntimeRequirement values")
        if not all(isinstance(item, RuntimeCapability) for item in self.preferred):
            raise TypeError("preferred values must be RuntimeCapability values")
        required_capabilities = [item.capability for item in self.required]
        if len(required_capabilities) != len(set(required_capabilities)):
            raise ValueError("runtime requirements must be unique")
        if len(self.preferred) != len(set(self.preferred)):
            raise ValueError("preferred runtime capabilities must be unique")

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_payload(self) -> dict[str, object]:
        return {
            "required": [
                item.to_payload()
                for item in sorted(
                    self.required,
                    key=lambda requirement: requirement.capability.value,
                )
            ],
            "preferred": sorted(item.value for item in self.preferred),
        }


@dataclass(frozen=True)
class RuntimeRejection:
    runtime_id: str
    unmet: tuple[RuntimeRequirement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "unmet", tuple(self.unmet))

    def to_payload(self) -> dict[str, object]:
        return {
            "runtime_id": self.runtime_id,
            "unmet": [item.to_payload() for item in self.unmet],
        }


@dataclass(frozen=True)
class RuntimeSelectionDecision:
    request_fingerprint: str
    selected_runtime_id: str | None
    selected_profile_fingerprint: str | None
    eligible_runtime_ids: tuple[str, ...]
    rejected: tuple[RuntimeRejection, ...]
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "eligible_runtime_ids", tuple(self.eligible_runtime_ids))
        object.__setattr__(self, "rejected", tuple(self.rejected))

    def to_payload(self) -> dict[str, object]:
        return {
            "request_fingerprint": self.request_fingerprint,
            "selected_runtime_id": self.selected_runtime_id,
            "selected_profile_fingerprint": self.selected_profile_fingerprint,
            "eligible_runtime_ids": list(self.eligible_runtime_ids),
            "rejected": [item.to_payload() for item in self.rejected],
            "rationale": self.rationale,
        }


class RuntimeCapabilitySelector:
    """Select only attested backends; return explicit gaps when none qualify."""

    def select(
        self,
        profiles: tuple[RuntimeCapabilityProfile, ...],
        request: RuntimeSelectionRequest,
    ) -> RuntimeSelectionDecision:
        runtime_ids = [profile.runtime_id for profile in profiles]
        if len(runtime_ids) != len(set(runtime_ids)):
            raise ValueError("runtime profile ids must be unique")
        eligible = []
        rejected = []
        for profile in profiles:
            unmet = tuple(
                requirement
                for requirement in sorted(
                    request.required,
                    key=lambda item: item.capability.value,
                )
                if not profile.supports(requirement)
            )
            if unmet:
                rejected.append(RuntimeRejection(profile.runtime_id, unmet))
            else:
                eligible.append(profile)
        ordered = sorted(
            eligible,
            key=lambda profile: (
                -sum(
                    profile.assurance(capability) is CapabilityAssurance.VERIFIED
                    for capability in request.preferred
                ),
                len(profile.known_gaps),
                profile.priority,
                profile.runtime_id,
            ),
        )
        selected = ordered[0] if ordered else None
        return RuntimeSelectionDecision(
            request_fingerprint=request.fingerprint(),
            selected_runtime_id=selected.runtime_id if selected else None,
            selected_profile_fingerprint=selected.fingerprint() if selected else None,
            eligible_runtime_ids=tuple(profile.runtime_id for profile in ordered),
            rejected=tuple(sorted(rejected, key=lambda item: item.runtime_id)),
            rationale=(
                "Selected the highest-assurance deterministic eligible Runtime."
                if selected
                else "No Runtime satisfies every required capability at the requested assurance."
            ),
        )
