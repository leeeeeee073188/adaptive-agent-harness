from __future__ import annotations

import unittest

from adaptive_harness.distiller import DeterministicFakeDistiller
from adaptive_harness.experience_evolution import OfflineExperienceEvolution
from adaptive_harness.experience_store import (
    ExperienceStatus,
    ExperienceStore,
    RetrievalQuery,
    TransferValidation,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.ledger_rollout import PublicOutcome, ledger_to_development_rollout


def _development_ledger(task_id: str) -> SessionLedger:
    ledger = SessionLedger(f"run-{task_id}")
    ledger.append(
        "state/updated",
        {
            "delta": {
                "task_id": task_id,
                "partition": "development",
                "task_state": "tool_recovery",
                "runtime_surface": "cli",
            },
            "reason": "public runtime state",
        },
    )
    ledger.append(
        "tool/result",
        {
            "call_id": "call-1",
            "content": "public timeout",
            "error_type": "TIMEOUT",
            "metadata": {"attempts": 1},
        },
    )
    ledger.append(
        "progress/checked",
        {"status": "no_progress", "reason": "no public task fact changed"},
    )
    ledger.append(
        "completion/checked",
        {"passed": False, "feedback": "public completion rejected"},
    )
    return ledger


class OfflineEvolutionEndToEndTests(unittest.TestCase):
    def test_ledger_to_candidate_to_transfer_promoted_store_without_model_calls(self) -> None:
        development_ids = {"dev-a", "dev-b", "dev-c"}
        outcomes = {
            "dev-a": PublicOutcome(1.0, True),
            "dev-b": PublicOutcome(0.0, False),
            "dev-c": PublicOutcome(0.5, False),
        }
        rollouts = tuple(
            ledger_to_development_rollout(
                _development_ledger(task_id),
                trusted_development_task_ids=development_ids,
                blocked_task_ids={"transfer-a", "transfer-b", "heldout-a"},
                outcome=outcomes[task_id],
            )
            for task_id in sorted(development_ids)
        )
        store = ExperienceStore(SessionLedger("offline-e2e-store"))
        evolution = OfflineExperienceEvolution(
            store,
            DeterministicFakeDistiller(),
            development_task_ids=development_ids,
        )

        candidates = evolution.distill_candidates(rollouts)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(store.state.lifecycle[candidate.key], ExperienceStatus.CANDIDATE)
        store.start_shadow(candidate.experience_id, 1, reason="offline checks passed")
        store.promote(
            candidate.experience_id,
            1,
            TransferValidation(
                ("transfer-a", "transfer-b"),
                stable_pass_regressions=0,
                harm_observed=False,
                evidence_ref="evidence://transfer-pair",
                effective_task_ids=("transfer-a",),
                no_progress_regressions=0,
            ),
        )
        retrieved = store.retrieve(
            RetrievalQuery("tool_recovery", "TIMEOUT", "cli")
        )

        self.assertEqual(retrieved, (candidate,))
        self.assertEqual(store.state.lifecycle[candidate.key], ExperienceStatus.PROMOTED)
        self.assertNotIn("heldout-a", str(candidate))


if __name__ == "__main__":
    unittest.main()
