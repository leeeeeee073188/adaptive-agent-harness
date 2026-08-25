from __future__ import annotations

import unittest

from adaptive_harness.integrations.runtime_profiles import (
    browser_task_runtime_request,
    deerflow_runtime_profile,
    deterministic_reference_runtime_profile,
    file_task_runtime_request,
)
from adaptive_harness.runtime_capabilities import (
    CapabilityAssurance,
    CapabilityAttestation,
    RuntimeCapability,
    RuntimeCapabilityProfile,
    RuntimeCapabilitySelector,
    RuntimeRequirement,
    RuntimeSelectionRequest,
)


class RuntimeCapabilityTests(unittest.TestCase):
    def test_verified_capability_requires_public_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires evidence_ref"):
            CapabilityAttestation(
                RuntimeCapability.FILE_READ,
                CapabilityAssurance.VERIFIED,
            )
        with self.assertRaisesRegex(ValueError, "approved public scheme"):
            CapabilityAttestation(
                RuntimeCapability.FILE_READ,
                CapabilityAssurance.VERIFIED,
                evidence_ref="https://example.com/unpinned",
            )
        with self.assertRaisesRegex(ValueError, "material evidence"):
            CapabilityAttestation(
                RuntimeCapability.FILE_READ,
                CapabilityAssurance.VERIFIED,
                evidence_ref="docs://self-claim",
            )

    def test_profile_rejects_duplicates_and_credentials(self) -> None:
        attestation = CapabilityAttestation(
            RuntimeCapability.FILE_READ,
            CapabilityAssurance.VERIFIED,
            evidence_ref="test://file-read",
        )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            RuntimeCapabilityProfile(
                "duplicate",
                "1",
                (attestation, attestation),
            )
        with self.assertRaisesRegex(ValueError, "credentials"):
            RuntimeCapabilityProfile(
                "secret-limit",
                "1",
                (attestation,),
                limits=(("api_key", "hidden"),),
            )
        with self.assertRaisesRegex(TypeError, "scalar values"):
            RuntimeCapabilityProfile(
                "nested-limit",
                "1",
                (attestation,),
                limits=(("nested", {"value": 1}),),  # type: ignore[arg-type]
            )

    def test_caller_owned_sequences_are_canonicalized_before_validation(self) -> None:
        limitations = ["initial"]
        attestation = CapabilityAttestation(
            RuntimeCapability.FILE_READ,
            CapabilityAssurance.VERIFIED,
            evidence_ref="test://file-read",
            limitations=limitations,  # type: ignore[arg-type]
        )
        attestations = [attestation]
        known_gaps = ["gap"]
        limits = [("calls", 1)]
        profile = RuntimeCapabilityProfile(
            "immutable",
            "1",
            attestations,  # type: ignore[arg-type]
            known_gaps=known_gaps,  # type: ignore[arg-type]
            limits=limits,  # type: ignore[arg-type]
        )
        requirements = [RuntimeRequirement(RuntimeCapability.FILE_READ)]
        preferred = [RuntimeCapability.BEFORE_RUN_EVIDENCE]
        request = RuntimeSelectionRequest(
            required=requirements,  # type: ignore[arg-type]
            preferred=preferred,  # type: ignore[arg-type]
        )
        profile_fingerprint = profile.fingerprint()
        request_fingerprint = request.fingerprint()

        limitations.append("mutated")
        attestations.clear()
        known_gaps.append("mutated")
        limits.append(("calls", 2))
        requirements.clear()
        preferred.clear()

        self.assertEqual(attestation.limitations, ("initial",))
        self.assertEqual(profile.fingerprint(), profile_fingerprint)
        self.assertEqual(request.fingerprint(), request_fingerprint)

    def test_profile_fingerprint_is_order_independent_and_assurance_sensitive(self) -> None:
        file_read = CapabilityAttestation(
            RuntimeCapability.FILE_READ,
            CapabilityAssurance.VERIFIED,
            evidence_ref="test://file-read",
        )
        file_write = CapabilityAttestation(
            RuntimeCapability.FILE_WRITE,
            CapabilityAssurance.VERIFIED,
            evidence_ref="test://file-write",
        )
        first = RuntimeCapabilityProfile("runtime", "1", (file_read, file_write))
        reordered = RuntimeCapabilityProfile("runtime", "1", (file_write, file_read))
        claimed = RuntimeCapabilityProfile(
            "runtime",
            "1",
            (
                file_read,
                CapabilityAttestation(
                    RuntimeCapability.FILE_WRITE,
                    CapabilityAssurance.CLAIMED,
                    evidence_ref="docs://file-write",
                ),
            ),
        )

        self.assertEqual(first.fingerprint(), reordered.fingerprint())
        self.assertNotEqual(first.fingerprint(), claimed.fingerprint())

    def test_strict_file_request_rejects_every_current_runtime(self) -> None:
        decision = RuntimeCapabilitySelector().select(
            (
                deerflow_runtime_profile(),
                deterministic_reference_runtime_profile(),
            ),
            file_task_runtime_request(),
        )

        self.assertIsNone(decision.selected_runtime_id)
        rejected = {item.runtime_id: item for item in decision.rejected}
        self.assertIn(
            RuntimeCapability.FINAL_RESPONSE,
            {item.capability for item in rejected["deerflow"].unmet},
        )
        self.assertIn(
            RuntimeCapability.SHELL,
            {
                item.capability
                for item in rejected["deterministic-reference"].unmet
            },
        )

    def test_claimed_final_response_can_select_deerflow_only_when_explicit(self) -> None:
        decision = RuntimeCapabilitySelector().select(
            (
                deterministic_reference_runtime_profile(),
                deerflow_runtime_profile(),
            ),
            file_task_runtime_request(
                final_response_assurance=CapabilityAssurance.CLAIMED
            ),
        )

        self.assertEqual(decision.selected_runtime_id, "deerflow")
        self.assertEqual(decision.eligible_runtime_ids, ("deerflow",))

    def test_browser_requires_verified_browser_capability(self) -> None:
        decision = RuntimeCapabilitySelector().select(
            (deerflow_runtime_profile(),),
            browser_task_runtime_request(),
        )

        self.assertIsNone(decision.selected_runtime_id)
        self.assertIn(
            RuntimeCapability.BROWSER,
            {item.capability for item in decision.rejected[0].unmet},
        )

    def test_reference_runtime_is_selected_for_control_plane_conformance(self) -> None:
        request = RuntimeSelectionRequest(
            required=(
                RuntimeRequirement(RuntimeCapability.MULTI_TURN),
                RuntimeRequirement(RuntimeCapability.FINAL_RESPONSE),
                RuntimeRequirement(RuntimeCapability.DURABLE_LEDGER),
            ),
            preferred=(RuntimeCapability.BEFORE_RUN_EVIDENCE,),
        )
        profiles = (
            deerflow_runtime_profile(),
            deterministic_reference_runtime_profile(),
        )

        forward = RuntimeCapabilitySelector().select(profiles, request)
        reversed_order = RuntimeCapabilitySelector().select(
            tuple(reversed(profiles)),
            request,
        )

        self.assertEqual(forward.selected_runtime_id, "deterministic-reference")
        self.assertEqual(forward.to_payload(), reversed_order.to_payload())
        self.assertEqual(len(forward.request_fingerprint), 64)

    def test_duplicate_request_or_runtime_ids_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "requirements must be unique"):
            RuntimeSelectionRequest(
                required=(
                    RuntimeRequirement(RuntimeCapability.FILE_READ),
                    RuntimeRequirement(RuntimeCapability.FILE_READ),
                )
            )
        profile = deterministic_reference_runtime_profile()
        with self.assertRaisesRegex(ValueError, "profile ids must be unique"):
            RuntimeCapabilitySelector().select(
                (profile, profile),
                RuntimeSelectionRequest(
                    required=(RuntimeRequirement(RuntimeCapability.MULTI_TURN),)
                ),
            )


if __name__ == "__main__":
    unittest.main()
