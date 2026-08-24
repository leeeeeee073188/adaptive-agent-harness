from __future__ import annotations

import unittest

from adaptive_harness.source_grounding import (
    ArtifactDerivation,
    ClaimAtom,
    DerivationKind,
    GroundingRequirements,
    LineageGraph,
    SourceGroundingGate,
    SourceHandle,
    SourceRole,
)


def _hash(character: str) -> str:
    return character * 64


class SourceGroundingTests(unittest.TestCase):
    def test_authoritative_transform_with_supported_claims_passes(self) -> None:
        graph = LineageGraph(
            sources=(
                SourceHandle(
                    "raw-a",
                    "http://127.0.0.1:4500/api/data/a",
                    _hash("a"),
                    SourceRole.AUTHORITATIVE,
                    origin_event_seq=12,
                ),
            ),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("b"),
                ("raw-a",),
                DerivationKind.TRANSFORM,
                claims=(ClaimAtom("claim-1", _hash("c"), ("raw-a",)),),
            ),
        )

        decision = SourceGroundingGate().evaluate(graph, GroundingRequirements())

        self.assertTrue(decision.passed)
        self.assertEqual(decision.authoritative_source_count, 1)
        self.assertEqual(decision.missing_source_ids, ())

    def test_exact_copy_of_provisional_source_is_rejected(self) -> None:
        graph = LineageGraph(
            sources=(
                SourceHandle(
                    "draft",
                    "workspace/analysis/results.json",
                    _hash("a"),
                    SourceRole.PROVISIONAL,
                ),
                SourceHandle(
                    "raw",
                    "http://127.0.0.1:4500/api/data",
                    _hash("b"),
                    SourceRole.AUTHORITATIVE,
                ),
            ),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("a"),
                ("draft", "raw"),
                DerivationKind.SYNTHESIS,
            ),
        )

        decision = SourceGroundingGate().evaluate(graph, GroundingRequirements())

        self.assertFalse(decision.passed)
        self.assertEqual(decision.exact_provisional_copies, ("draft",))

    def test_model_prose_cannot_be_the_only_support(self) -> None:
        graph = LineageGraph(
            sources=(
                SourceHandle(
                    "assistant-note",
                    "assistant/message/12",
                    _hash("a"),
                    SourceRole.MODEL_PROSE,
                ),
            ),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("b"),
                ("assistant-note",),
                DerivationKind.SYNTHESIS,
                claims=(ClaimAtom("claim-1", _hash("c"), ("assistant-note",)),),
            ),
        )

        decision = SourceGroundingGate().evaluate(graph, GroundingRequirements())

        self.assertFalse(decision.passed)
        self.assertEqual(decision.forbidden_source_ids, ("assistant-note",))
        self.assertEqual(decision.authoritative_source_count, 0)

    def test_one_authoritative_source_may_support_multiple_claims(self) -> None:
        source = SourceHandle(
            "raw",
            "workspace/raw.json",
            _hash("a"),
            SourceRole.AUTHORITATIVE,
        )
        graph = LineageGraph(
            sources=(source,),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("b"),
                ("raw",),
                DerivationKind.AGGREGATE,
                claims=(
                    ClaimAtom("claim-1", _hash("c"), ("raw",)),
                    ClaimAtom("claim-2", _hash("d"), ("raw",)),
                ),
            ),
        )

        decision = SourceGroundingGate().evaluate(graph, GroundingRequirements())

        self.assertTrue(decision.passed)

    def test_explicit_direct_copy_of_final_eligible_authority_is_allowed(self) -> None:
        graph = LineageGraph(
            sources=(
                SourceHandle(
                    "approved",
                    "workspace/approved.json",
                    _hash("a"),
                    SourceRole.AUTHORITATIVE,
                    final_eligible=True,
                ),
            ),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("a"),
                ("approved",),
                DerivationKind.DIRECT_COPY,
            ),
        )

        decision = SourceGroundingGate().evaluate(graph, GroundingRequirements())

        self.assertTrue(decision.passed)

    def test_lineage_payload_round_trip_is_replayable(self) -> None:
        graph = LineageGraph(
            sources=(
                SourceHandle(
                    "raw",
                    "workspace/raw.json",
                    _hash("a"),
                    SourceRole.AUTHORITATIVE,
                    origin_event_seq=7,
                    final_eligible=True,
                ),
            ),
            artifact=ArtifactDerivation(
                "outputs/report.json",
                _hash("b"),
                ("raw",),
                DerivationKind.TRANSFORM,
                claims=(ClaimAtom("claim-1", _hash("c"), ("raw",)),),
            ),
        )

        restored = LineageGraph.from_payload(graph.to_payload())

        self.assertEqual(restored, graph)


if __name__ == "__main__":
    unittest.main()
