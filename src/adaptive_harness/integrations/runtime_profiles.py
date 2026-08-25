"""Evidence-backed Runtime capability profiles; no benchmark policy in Core."""

from __future__ import annotations

from adaptive_harness.runtime_capabilities import (
    CapabilityAssurance,
    CapabilityAttestation,
    RuntimeCapability,
    RuntimeCapabilityProfile,
    RuntimeRequirement,
    RuntimeSelectionRequest,
)


def deerflow_runtime_profile() -> RuntimeCapabilityProfile:
    verified = (
        RuntimeCapability.TOOL_CALLING,
        RuntimeCapability.FILE_READ,
        RuntimeCapability.FILE_WRITE,
        RuntimeCapability.SHELL,
        RuntimeCapability.SANDBOX,
        RuntimeCapability.MULTI_TURN,
        RuntimeCapability.ARTIFACT_FILE,
        RuntimeCapability.ARTIFACT_DIRECTORY,
        RuntimeCapability.BEFORE_RUN_EVIDENCE,
        RuntimeCapability.STRUCTURED_OBSERVATION,
        RuntimeCapability.DURABLE_LEDGER,
    )
    attestations = [
        CapabilityAttestation(
            capability,
            CapabilityAssurance.VERIFIED,
            evidence_ref="evidence://a64-v4-2-transform-evidence-container",
        )
        for capability in verified
    ]
    attestations.extend(
        (
            CapabilityAttestation(
                RuntimeCapability.FINAL_RESPONSE,
                CapabilityAssurance.CLAIMED,
                evidence_ref="evidence://a66-deerflow-runtime-attribution-live",
                limitations=("Fresh File control returned no final response after retry.",),
            ),
            CapabilityAttestation(
                RuntimeCapability.BROWSER,
                CapabilityAssurance.CLAIMED,
                evidence_ref="evidence://a67-v4-2-minibench16-live",
                limitations=("Current v4.2 Browser tasks passed 0/5.",),
            ),
            CapabilityAttestation(
                RuntimeCapability.VISION_INPUT,
                CapabilityAssurance.CLAIMED,
                evidence_ref="runtime://deerflow-config-v4-2",
                limitations=("Configured support is not a successful task attestation.",),
            ),
        )
    )
    return RuntimeCapabilityProfile(
        runtime_id="deerflow",
        implementation_version="0debff98c1caf4a7d3047e8ef162d85a841b5c6d",
        attestations=tuple(attestations),
        known_gaps=(
            "final_response_reliability_unverified",
            "sandbox_path_false_positives",
            "browser_success_unverified",
        ),
        limits=(
            ("max_completion_turns", 3),
            ("max_tool_calls_per_turn", 20),
        ),
        priority=100,
    )


def deterministic_reference_runtime_profile() -> RuntimeCapabilityProfile:
    verified = (
        RuntimeCapability.MULTI_TURN,
        RuntimeCapability.FINAL_RESPONSE,
        RuntimeCapability.BEFORE_RUN_EVIDENCE,
        RuntimeCapability.STRUCTURED_OBSERVATION,
        RuntimeCapability.DURABLE_LEDGER,
    )
    return RuntimeCapabilityProfile(
        runtime_id="deterministic-reference",
        implementation_version="1",
        attestations=tuple(
            CapabilityAttestation(
                capability,
                CapabilityAssurance.VERIFIED,
                evidence_ref="test://runtime-conformance",
            )
            for capability in verified
        ),
        known_gaps=("no_real_environment_tools",),
        priority=10,
    )


def file_task_runtime_request(
    *,
    final_response_assurance: CapabilityAssurance = CapabilityAssurance.VERIFIED,
) -> RuntimeSelectionRequest:
    required = (
        RuntimeCapability.TOOL_CALLING,
        RuntimeCapability.FILE_READ,
        RuntimeCapability.FILE_WRITE,
        RuntimeCapability.SHELL,
        RuntimeCapability.SANDBOX,
        RuntimeCapability.MULTI_TURN,
        RuntimeCapability.ARTIFACT_FILE,
        RuntimeCapability.STRUCTURED_OBSERVATION,
        RuntimeCapability.DURABLE_LEDGER,
    )
    return RuntimeSelectionRequest(
        required=tuple(RuntimeRequirement(capability) for capability in required)
        + (
            RuntimeRequirement(
                RuntimeCapability.FINAL_RESPONSE,
                minimum_assurance=final_response_assurance,
            ),
        ),
        preferred=(RuntimeCapability.BEFORE_RUN_EVIDENCE,),
    )


def browser_task_runtime_request() -> RuntimeSelectionRequest:
    file_request = file_task_runtime_request()
    return RuntimeSelectionRequest(
        required=file_request.required
        + (RuntimeRequirement(RuntimeCapability.BROWSER),),
        preferred=file_request.preferred,
    )
