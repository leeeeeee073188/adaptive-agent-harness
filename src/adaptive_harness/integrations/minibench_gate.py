"""Zero-model integrity and paid-canary gates for the frozen MiniBench16."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from adaptive_harness.integrations.realreplica import (
    EvaluationRole,
    MiniBenchDataset,
)

_EXPECTED_ROLE_COUNTS = {
    EvaluationRole.DEVELOPMENT: 8,
    EvaluationRole.TRANSFER: 4,
    EvaluationRole.HELDOUT: 4,
}


@dataclass(frozen=True)
class PartitionIntegrityReport:
    passed: bool
    role_counts: dict[str, int]
    reasons: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "role_counts": dict(self.role_counts),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PaidCanaryEvidence:
    partition_integrity_passed: bool
    runtime_conformance_passed: bool
    container_wiring_passed: bool
    historical_baseline_available: bool
    stable_regression_controls_passed: bool
    profile_reproducible: bool
    credentials_external: bool
    single_canary_default: bool
    target_role: EvaluationRole = EvaluationRole.DEVELOPMENT


@dataclass(frozen=True)
class PaidCanaryDecision:
    allowed: bool
    blockers: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {"allowed": self.allowed, "blockers": list(self.blockers)}


def evaluate_partition_integrity(dataset: MiniBenchDataset) -> PartitionIntegrityReport:
    reasons: list[str] = []
    task_ids = [task.task_id for task in dataset.tasks]
    if len(task_ids) != len(set(task_ids)):
        reasons.append("MiniBench task ids are not unique")
    counts = Counter(task.evaluation_role for task in dataset.tasks)
    for role, expected in _EXPECTED_ROLE_COUNTS.items():
        if counts[role] != expected:
            reasons.append(
                f"{role.value} task count {counts[role]} does not equal {expected}"
            )
        tasks = dataset.tasks_for_role(role)
        categories = {task.category for task in tasks}
        difficulties = {task.difficulty_band for task in tasks}
        capabilities = {task.capability for task in tasks}
        if len(categories) < 4:
            reasons.append(f"{role.value} does not cover all four task categories")
        if len(difficulties) < 2:
            reasons.append(f"{role.value} covers fewer than two difficulty bands")
        if len(capabilities) < 2 or "vision" not in capabilities:
            reasons.append(f"{role.value} lacks multimodal capability diversity")
        if tasks and all(task.smoke_reuse for task in tasks):
            reasons.append(f"{role.value} contains only reused smoke tasks")
    role_counts = {
        role.value: counts[role]
        for role in (
            EvaluationRole.DEVELOPMENT,
            EvaluationRole.TRANSFER,
            EvaluationRole.HELDOUT,
        )
    }
    return PartitionIntegrityReport(not reasons, role_counts, tuple(reasons))


def decide_paid_canary(evidence: PaidCanaryEvidence) -> PaidCanaryDecision:
    checks = {
        "partition integrity has not passed": evidence.partition_integrity_passed,
        "runtime conformance has not passed": evidence.runtime_conformance_passed,
        "container wiring has not passed": evidence.container_wiring_passed,
        "historical baseline is unavailable": evidence.historical_baseline_available,
        "stable regression controls have not passed": (
            evidence.stable_regression_controls_passed
        ),
        "Profile fingerprint is not reproducible": evidence.profile_reproducible,
        "credentials are not externalized": evidence.credentials_external,
        "Development config does not default to one canary": (
            evidence.single_canary_default
        ),
    }
    blockers = [message for message, passed in checks.items() if not passed]
    if evidence.target_role is EvaluationRole.HELDOUT:
        blockers.append("Held-out requires a separately promoted final Candidate")
    return PaidCanaryDecision(not blockers, tuple(blockers))
