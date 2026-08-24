from __future__ import annotations

import json
import unittest

from adaptive_harness.distiller import (
    ConstrainedModelDistiller,
    DeterministicFakeDistiller,
    DistillerConfig,
    DistillerSchemaError,
)
from adaptive_harness.experience_evolution import ExperienceGroupTrigger, SanitizedRollout

TRIGGER = ExperienceGroupTrigger("tool_recovery", "TIMEOUT", "cli")
ROLLOUTS = (
    SanitizedRollout(
        1.0,
        True,
        (
            {"type": "tool/result", "payload": {"error_type": "TIMEOUT", "result_changed": True}},
        ),
    ),
    SanitizedRollout(
        0.0,
        False,
        (
            {"type": "progress/checked", "payload": {"status": "no_progress"}},
        ),
    ),
)


GOOD_DRAFT = {
    "experience_id": "recover-timeout-with-narrowed-evidence",
    "situation": "A command times out before producing actionable evidence.",
    "strategy": "Inspect partial state, narrow the operation, then retry once with an observable checkpoint.",
    "anti_pattern": "Do not repeat the same timeout-prone action without changing scope.",
    "progress_signal": "The narrower action returns new state or a clearer failure classification.",
    "stop_condition": "Stop after the scoped retry also produces no new evidence.",
}


class DeterministicFakeDistillerTests(unittest.TestCase):
    def test_fake_distiller_is_stable_and_transferable_without_model_calls(self) -> None:
        distiller = DeterministicFakeDistiller()

        first = distiller.distill(TRIGGER, ROLLOUTS)
        second = distiller.distill(TRIGGER, ROLLOUTS)

        self.assertEqual(first, second)
        rendered = " ".join(
            (
                first.experience_id,
                first.situation,
                first.strategy,
                first.anti_pattern,
                first.progress_signal,
                first.stop_condition,
            )
        )
        self.assertIn("timeout", rendered.casefold())
        self.assertNotIn("dev-a", rendered)
        self.assertNotIn("verifier", rendered.casefold())


class ConstrainedModelDistillerTests(unittest.TestCase):
    def test_model_adapter_is_disabled_by_default(self) -> None:
        calls: list[object] = []
        distiller = ConstrainedModelDistiller(
            lambda trigger, rollouts: calls.append((trigger, rollouts)) or GOOD_DRAFT
        )

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            distiller.distill(TRIGGER, ROLLOUTS)

        self.assertEqual(calls, [])

    def test_enabled_model_adapter_makes_one_injected_completion_call_and_parses_json(self) -> None:
        calls = []

        def complete(trigger: ExperienceGroupTrigger, rollouts: tuple[SanitizedRollout, ...]) -> str:
            calls.append((trigger, rollouts))
            return json.dumps(GOOD_DRAFT)

        distiller = ConstrainedModelDistiller(complete, config=DistillerConfig(enabled=True))

        draft = distiller.distill(TRIGGER, ROLLOUTS)

        self.assertEqual(draft.experience_id, GOOD_DRAFT["experience_id"])
        self.assertEqual(draft.strategy, GOOD_DRAFT["strategy"])
        self.assertEqual(calls, [(TRIGGER, ROLLOUTS)])

    def test_strict_schema_rejects_extra_missing_and_non_string_fields(self) -> None:
        cases = (
            {**GOOD_DRAFT, "extra": "not allowed"},
            {key: value for key, value in GOOD_DRAFT.items() if key != "strategy"},
            {**GOOD_DRAFT, "strategy": ["not", "a", "string"]},
            {**GOOD_DRAFT, "strategy": "   "},
            [GOOD_DRAFT],
            "not json",
        )
        for payload in cases:
            with self.subTest(payload=payload):
                distiller = ConstrainedModelDistiller(
                    lambda _trigger, _rollouts, payload=payload: payload,
                    config=DistillerConfig(enabled=True),
                )

                with self.assertRaises(DistillerSchemaError):
                    distiller.distill(TRIGGER, ROLLOUTS)

    def test_adapter_fails_closed_on_selectors_task_ids_and_private_answer_leakage(self) -> None:
        risky_values = (
            {**GOOD_DRAFT, "strategy": "Click #submit to finish."},
            {**GOOD_DRAFT, "strategy": "Use [data-answer=value] as a stable hook."},
            {**GOOD_DRAFT, "strategy": "Use //button to find the target."},
            {**GOOD_DRAFT, "situation": "This happened on dev-a."},
            {**GOOD_DRAFT, "anti_pattern": "Do not read the expected answer."},
            {**GOOD_DRAFT, "progress_signal": "The private verifier reports success."},
            {**GOOD_DRAFT, "stop_condition": "The answer key is known."},
            {**GOOD_DRAFT, "strategy": "Follow the judge reasoning."},
        )
        for payload in risky_values:
            with self.subTest(payload=payload):
                distiller = ConstrainedModelDistiller(
                    lambda _trigger, _rollouts, payload=payload: payload,
                    config=DistillerConfig(enabled=True),
                )

                with self.assertRaises(DistillerSchemaError):
                    distiller.distill(TRIGGER, ROLLOUTS)


if __name__ == "__main__":
    unittest.main()
