"""Rollout/judgement/practice records kept outside the online driver."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JudgeResult:
    reward: float
    reasoning: str | None = None


@dataclass(frozen=True)
class RolloutRecord:
    sample_id: str
    run_id: str
    response: str
    trajectory_ref: str
    judge: JudgeResult | None = None
    variant: str = "unknown"
    passed: bool | None = None
    capacity_score: float | None = None
    usage: Mapping[str, int] = field(default_factory=dict)
    duration_sec: float | None = None


@dataclass(frozen=True)
class VariantMetrics:
    variant: str
    rollout_count: int
    pass_rate: float | None
    capacity_score_mean: float | None
    total_tokens: int
    duration_sec_total: float


@dataclass(frozen=True)
class PairedEvaluationStats:
    baseline: VariantMetrics
    candidate: VariantMetrics
    matched_samples: int
    pass_rate_delta: float | None
    capacity_score_delta: float | None
    total_token_delta: int


def summarize_paired_rollouts(
    records: Iterable[RolloutRecord],
    *,
    baseline_variant: str,
    candidate_variant: str,
) -> PairedEvaluationStats:
    """Aggregate only sample ids observed in both variants."""

    by_sample: dict[str, dict[str, RolloutRecord]] = defaultdict(dict)
    for record in records:
        if record.variant not in {baseline_variant, candidate_variant}:
            continue
        if record.variant in by_sample[record.sample_id]:
            raise ValueError(f"duplicate rollout for {record.sample_id!r}/{record.variant!r}")
        by_sample[record.sample_id][record.variant] = record
    matched = [
        pair
        for pair in by_sample.values()
        if baseline_variant in pair and candidate_variant in pair
    ]
    baseline = _variant_metrics(
        baseline_variant,
        [pair[baseline_variant] for pair in matched],
    )
    candidate = _variant_metrics(
        candidate_variant,
        [pair[candidate_variant] for pair in matched],
    )
    return PairedEvaluationStats(
        baseline=baseline,
        candidate=candidate,
        matched_samples=len(matched),
        pass_rate_delta=_delta(candidate.pass_rate, baseline.pass_rate),
        capacity_score_delta=_delta(candidate.capacity_score_mean, baseline.capacity_score_mean),
        total_token_delta=candidate.total_tokens - baseline.total_tokens,
    )


@dataclass(frozen=True)
class ExperienceCandidate:
    situation: str
    strategy: str
    anti_pattern: str
    provenance: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


class ExperienceAdmissibilityFilter:
    """Fail closed on benchmark-specific or answer-like experience content."""

    _FORBIDDEN = (
        re.compile(r"\b(?:cli|browser|file|api)-[a-z0-9-]+\b", re.IGNORECASE),
        re.compile(r"\b(?:ground[_ -]?truth|expected[_ -]?answer|verifier|rubric\.json)\b", re.IGNORECASE),
        re.compile(r"(?:#[a-zA-Z][\w-]*|\[data-[\w-]+=)", re.IGNORECASE),
    )

    def admit(self, candidate: ExperienceCandidate) -> bool:
        content = "\n".join(
            (
                candidate.situation,
                candidate.strategy,
                candidate.anti_pattern,
                *candidate.provenance,
                json.dumps(candidate.metadata, ensure_ascii=False, sort_keys=True),
            )
        )
        return bool(candidate.provenance) and not any(pattern.search(content) for pattern in self._FORBIDDEN)


def _variant_metrics(variant: str, records: list[RolloutRecord]) -> VariantMetrics:
    passed = [record.passed for record in records if record.passed is not None]
    scores = [record.capacity_score for record in records if record.capacity_score is not None]
    return VariantMetrics(
        variant=variant,
        rollout_count=len(records),
        pass_rate=(sum(passed) / len(passed) if passed else None),
        capacity_score_mean=(sum(scores) / len(scores) if scores else None),
        total_tokens=sum(int(record.usage.get("total_tokens", 0)) for record in records),
        duration_sec_total=sum(record.duration_sec or 0.0 for record in records),
    )


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline
