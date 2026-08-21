"""RealReplicaBench task semantics plugged into the generic contract builder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from adaptive_harness.task_contract import (
    CriterionDraft,
    CriterionKind,
    RuleBasedTaskContractBuilder,
)


@dataclass(frozen=True)
class _ObservationRule:
    subject: str
    description: str
    patterns: tuple[str, ...]


class RealReplicaCriterionExtractor:
    """Extract public state postconditions used by the benchmark integration.

    Keeping this vocabulary outside the core lets another environment supply
    its own ontology without changing Harness control logic.
    """

    _RULES = (
        _ObservationRule(
            "listing.submitted",
            "Listing submission is observed",
            (r"发上线|publish|submit", r"发品系统|listing|product|商品"),
        ),
        _ObservationRule(
            "mail.label_created",
            "Required mail label is observed",
            (r"创建(?:顶层)?标签|create (?:a )?(?:top-level )?label",),
        ),
        _ObservationRule(
            "mail.draft_saved",
            "Required unsent draft is observed",
            (r"未发送草稿|unsent draft|save (?:an? )?draft",),
        ),
        _ObservationRule(
            "calendar.event_created",
            "Required calendar event is observed",
            (r"日历事件|calendar event", r"再建|创建|create|schedule"),
        ),
        _ObservationRule(
            "document.updated",
            "Target document update is observed",
            (r"document|文档", r"apply it|update|更新|修改"),
        ),
    )

    def extract(self, task_prompt: str) -> tuple[CriterionDraft, ...]:
        drafts = []
        for rule in self._RULES:
            if not all(re.search(pattern, task_prompt, re.IGNORECASE) for pattern in rule.patterns):
                continue
            parameters: dict[str, Any] = {"subject": rule.subject, "expected": True}
            parameters.update(self._target_parameters(rule.subject, task_prompt))
            drafts.append(
                CriterionDraft(
                    identity=rule.subject,
                    description=rule.description,
                    kind=CriterionKind.OBSERVATION_EQUALS,
                    parameters=parameters,
                )
            )
        return tuple(drafts)

    def _target_parameters(self, subject: str, task_prompt: str) -> dict[str, Any]:
        patterns = {
            "mail.label_created": r"(?:顶层标签|label)\s*`([^`]+)`|`([^`]+)`\s*(?:标签|label)",
            "calendar.event_created": r"`([^`]+)`\s*(?:日历事件|calendar event)",
            "document.updated": r"(?:titled|标题为)\s*\**[\"“]([^\"”*]+)[\"”]\**",
        }
        pattern = patterns.get(subject)
        match = re.search(pattern, task_prompt, re.IGNORECASE) if pattern else None
        if match is not None:
            target = next((group for group in match.groups() if group), "").strip()
            return {"target": target} if target else {}
        if subject == "calendar.event_created":
            contains = re.search(
                r"标题里?要?带\s*`([^`]+)`\s*或\s*`([^`]+)`",
                task_prompt,
                re.IGNORECASE,
            )
            if contains:
                return {"target_any": list(contains.groups())}
        return {}


def realreplica_contract_builder() -> RuleBasedTaskContractBuilder:
    return RuleBasedTaskContractBuilder(extractors=(RealReplicaCriterionExtractor(),))

