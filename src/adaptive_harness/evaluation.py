"""Rollout/judgement/practice records kept outside the online driver."""

from __future__ import annotations

import re
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
        content = "\n".join((candidate.situation, candidate.strategy, candidate.anti_pattern))
        return bool(candidate.provenance) and not any(pattern.search(content) for pattern in self._FORBIDDEN)
