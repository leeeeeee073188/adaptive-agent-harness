from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.experience_store import (
    Experience,
    ExperienceLifecycle,
    ExperienceStatus,
    ExperienceStore,
    RetrievalOutcome,
    RetrievalQuery,
    TaskStateExperienceRetriever,
    TransferValidation,
)
from adaptive_harness.ledger import SessionLedger


def _experience(**overrides: object) -> Experience:
    values = {
        "experience_id": "recover-cli-timeout",
        "version": 1,
        "task_state": "tool_recovery",
        "failure_type": "cli_timeout",
        "runtime_surface": "cli",
        "situation": "A command times out before producing task evidence.",
        "strategy": "Inspect whether partial output changed state, then retry once with a narrower command.",
        "anti_pattern": "Repeating the same long-running command without new evidence.",
        "progress_signal": "A narrower command returns fresh evidence or a changed artifact timestamp.",
        "stop_condition": "Stop retrying when the narrower command also yields no new evidence.",
        "source_task_ids": ("dev-a", "dev-b", "dev-c"),
    }
    values.update(overrides)
    return Experience(**values)  # type: ignore[arg-type]


def _validation(**overrides: object) -> TransferValidation:
    values = {
        "validation_task_ids": ("transfer-a", "transfer-b"),
        "stable_pass_regressions": 0,
        "harm_observed": False,
        "evidence_ref": "eval://transfer-two-tasks",
        "effective_task_ids": ("transfer-a",),
        "no_progress_regressions": 0,
    }
    values.update(overrides)
    return TransferValidation(**values)  # type: ignore[arg-type]


class ExperienceStoreTests(unittest.TestCase):
    def test_candidate_requires_three_distinct_development_sources(self) -> None:
        store = ExperienceStore(SessionLedger("experience-sources"))

        with self.assertRaisesRegex(ValueError, "3 distinct Development source tasks"):
            store.create_candidate(_experience(source_task_ids=("dev-a", "dev-a", "dev-b")))

        store.create_candidate(_experience())

        self.assertEqual(
            store.state.lifecycle[("recover-cli-timeout", 1)],
            ExperienceStatus.CANDIDATE,
        )

    def test_admission_fails_closed_for_leakage_and_task_specific_content(self) -> None:
        risky_cases = (
            {"strategy": "Use the expected answer from the rollout notes."},
            {"situation": "The ground truth says the final count is already known."},
            {"anti_pattern": "Copy verifier-private rationale into the next attempt."},
            {"strategy": "Click selector #submit-answer from the source trajectory."},
            {"strategy": "Repeat the exact fix from dev-a."},
            {"strategy": "Click #submit-answer immediately."},
            {"situation": "browser-secret-task failed once."},
        )
        for index, overrides in enumerate(risky_cases):
            with self.subTest(overrides=overrides):
                store = ExperienceStore(SessionLedger(f"experience-leakage-{index}"))

                with self.assertRaisesRegex(ValueError, "admissibility"):
                    store.create_candidate(_experience(**overrides))

    def test_duplicate_transferable_content_is_rejected_across_ids_and_provenance(self) -> None:
        store = ExperienceStore(SessionLedger("experience-dedup"))
        store.create_candidate(_experience())

        with self.assertRaisesRegex(ValueError, "duplicate transferable Experience content"):
            store.create_candidate(
                _experience(
                    experience_id="same-strategy-new-name",
                    source_task_ids=("dev-d", "dev-e", "dev-f"),
                )
            )

    def test_transfer_validation_requires_disjoint_tasks_no_regression_or_harm(self) -> None:
        invalid_validations = (
            {"validation_task_ids": ("transfer-a",)},
            {"validation_task_ids": ("transfer-a", "dev-a")},
            {"stable_pass_regressions": 1},
            {"harm_observed": True},
            {"no_progress_regressions": 1},
            {"effective_task_ids": ()},
            {"effective_task_ids": ("not-a-transfer-task",)},
        )
        for index, overrides in enumerate(invalid_validations):
            with self.subTest(overrides=overrides):
                store = ExperienceStore(SessionLedger(f"experience-transfer-{index}"))
                store.create_candidate(_experience())
                store.start_shadow("recover-cli-timeout", 1, reason="passed offline admission")

                with self.assertRaisesRegex(ValueError, "transfer validation"):
                    store.promote("recover-cli-timeout", 1, _validation(**overrides))

                self.assertEqual(
                    store.state.lifecycle[("recover-cli-timeout", 1)],
                    ExperienceStatus.SHADOW,
                )

    def test_lifecycle_is_append_only_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experience.jsonl"
            ledger = SessionLedger("experience-lifecycle", path)
            store = ExperienceStore(ledger)
            store.create_candidate(_experience())
            store.start_shadow("recover-cli-timeout", 1, reason="offline checks passed")
            store.promote("recover-cli-timeout", 1, _validation())
            store.quarantine("recover-cli-timeout", 1, reason="possible obsolete strategy")
            store.retire("recover-cli-timeout", 1, reason="replacement promoted")

            replayed = ExperienceStore(SessionLedger.replay(path)).state

        self.assertEqual(
            replayed.lifecycle[("recover-cli-timeout", 1)],
            ExperienceStatus.RETIRED,
        )
        self.assertEqual(
            replayed.history[("recover-cli-timeout", 1)],
            (
                ExperienceLifecycle.CANDIDATE_CREATED,
                ExperienceLifecycle.SHADOW_STARTED,
                ExperienceLifecycle.PROMOTED,
                ExperienceLifecycle.QUARANTINED,
                ExperienceLifecycle.RETIRED,
            ),
        )

    def test_retrieval_filters_structured_fields_first_and_returns_at_most_three_promoted(self) -> None:
        store = ExperienceStore(SessionLedger("experience-retrieval"))
        candidates = [
            _experience(
                experience_id=f"cli-{index}",
                version=1,
                situation=f"CLI recovery situation variant {index}.",
            )
            for index in range(4)
        ] + [
            _experience(experience_id="browser-match", runtime_surface="browser"),
            _experience(experience_id="wrong-failure", failure_type="missing_constraint"),
        ]
        for candidate in candidates:
            store.create_candidate(candidate)
            store.start_shadow(candidate.experience_id, candidate.version, reason="offline checks passed")
            store.promote(candidate.experience_id, candidate.version, _validation())

        matches = store.retrieve(
            RetrievalQuery(
                task_state="tool_recovery",
                failure_type="cli_timeout",
                runtime_surface="cli",
            )
        )

        self.assertEqual([match.experience_id for match in matches], ["cli-0", "cli-1", "cli-2"])
        self.assertTrue(all(match.runtime_surface == "cli" for match in matches))
        self.assertTrue(all(match.failure_type == "cli_timeout" for match in matches))

    def test_runtime_retrieval_cannot_promote_or_mutate_lifecycle(self) -> None:
        store = ExperienceStore(SessionLedger("experience-runtime-readonly"))
        store.create_candidate(_experience())
        before_events = len(store.ledger.events)

        matches = store.retrieve(
            RetrievalQuery(
                task_state="tool_recovery",
                failure_type="cli_timeout",
                runtime_surface="cli",
            )
        )

        self.assertEqual(matches, ())
        self.assertEqual(len(store.ledger.events), before_events)
        self.assertEqual(
            store.state.lifecycle[("recover-cli-timeout", 1)],
            ExperienceStatus.CANDIDATE,
        )

    def test_task_state_retriever_accepts_flat_structured_state(self) -> None:
        store = ExperienceStore(SessionLedger("experience-flat-state"))
        store.create_candidate(_experience())
        store.start_shadow("recover-cli-timeout", 1, reason="checks passed")
        store.promote("recover-cli-timeout", 1, _validation())

        matches = TaskStateExperienceRetriever(store).retrieve(
            {
                "task_state": "tool_recovery",
                "failure_type": "cli_timeout",
                "runtime_surface": "cli",
            }
        )

        self.assertEqual([item.experience_id for item in matches], ["recover-cli-timeout"])

    def test_retrieval_outcome_is_attributed_without_changing_lifecycle(self) -> None:
        store = ExperienceStore(SessionLedger("experience-outcome"))
        store.create_candidate(_experience())
        store.start_shadow("recover-cli-timeout", 1, reason="checks passed")
        store.promote("recover-cli-timeout", 1, _validation())

        store.record_retrieval_outcome(
            "recover-cli-timeout",
            1,
            RetrievalOutcome(
                task_ref="run://transfer-c",
                adopted=True,
                progress_signal_observed=True,
                task_success=True,
                harm_observed=False,
                evidence_ref="ledger://transfer-c/outcome",
            ),
        )

        state = store.state
        self.assertEqual(
            state.lifecycle[("recover-cli-timeout", 1)],
            ExperienceStatus.PROMOTED,
        )
        self.assertTrue(
            state.retrieval_outcomes[("recover-cli-timeout", 1)][0].task_success
        )

    def test_unadopted_experience_cannot_claim_progress_success_or_harm(self) -> None:
        outcome = RetrievalOutcome(
            task_ref="run://not-adopted",
            adopted=False,
            progress_signal_observed=True,
            task_success=False,
            harm_observed=False,
            evidence_ref="ledger://not-adopted",
        )

        with self.assertRaisesRegex(ValueError, "unadopted Experience"):
            outcome.validate()


if __name__ == "__main__":
    unittest.main()
