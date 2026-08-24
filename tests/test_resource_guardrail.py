from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from adaptive_harness.resource_guardrail import (
    GuardrailObservation,
    NonMutatingTurnBudget,
    NoProgressDisposition,
    ResourceGuardrail,
)


def _observation(**overrides: object) -> GuardrailObservation:
    values = {
        "action_scope": "read:workspace/report.csv",
        "strategy_fingerprint": "same-read-arguments",
        "mutation_epoch": 0,
        "semantic_progress": False,
        "post_mutation_verification": False,
    }
    values.update(overrides)
    return GuardrailObservation(**values)  # type: ignore[arg-type]


class ResourceGuardrailTests(unittest.TestCase):
    def test_concurrent_nonmutating_admission_never_exceeds_turn_budget(self) -> None:
        budget = NonMutatingTurnBudget(20)

        with ThreadPoolExecutor(max_workers=32) as executor:
            admitted = list(
                executor.map(
                    lambda _index: budget.admit("turn-1", mutating=False),
                    range(100),
                )
            )

        self.assertEqual(sum(admitted), 20)
        self.assertEqual(budget.count("turn-1"), 20)
        self.assertTrue(budget.admit("turn-1", mutating=True))
        self.assertEqual(budget.count("turn-1"), 20)

    def test_no_progress_escalates_record_replan_then_block_scope(self) -> None:
        guardrail = ResourceGuardrail()

        decisions = [guardrail.observe(_observation()) for _ in range(3)]

        self.assertEqual(
            [decision.disposition for decision in decisions],
            [
                NoProgressDisposition.RECORD,
                NoProgressDisposition.REPLAN,
                NoProgressDisposition.BLOCK_SCOPE,
            ],
        )
        self.assertEqual(decisions[-1].consecutive_no_progress, 3)

    def test_semantic_progress_or_changed_strategy_resets_the_streak(self) -> None:
        guardrail = ResourceGuardrail()
        guardrail.observe(_observation())
        guardrail.observe(_observation())

        progressed = guardrail.observe(_observation(semantic_progress=True))
        changed = guardrail.observe(_observation(strategy_fingerprint="narrower-read"))
        repeated_changed = guardrail.observe(
            _observation(strategy_fingerprint="narrower-read")
        )

        self.assertEqual(progressed.disposition, NoProgressDisposition.ALLOW)
        self.assertEqual(changed.disposition, NoProgressDisposition.RECORD)
        self.assertEqual(repeated_changed.disposition, NoProgressDisposition.REPLAN)

    def test_first_verification_after_successful_mutation_is_not_no_progress(self) -> None:
        guardrail = ResourceGuardrail()
        guardrail.observe(_observation())

        verification = guardrail.observe(
            _observation(mutation_epoch=1, post_mutation_verification=True)
        )
        repeated = guardrail.observe(
            _observation(mutation_epoch=1, post_mutation_verification=True)
        )

        self.assertEqual(verification.disposition, NoProgressDisposition.ALLOW)
        self.assertEqual(repeated.disposition, NoProgressDisposition.RECORD)

    def test_empty_scope_or_strategy_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            _observation(action_scope="")


if __name__ == "__main__":
    unittest.main()
