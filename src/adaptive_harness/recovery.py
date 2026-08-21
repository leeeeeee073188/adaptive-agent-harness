"""Bounded task-level recovery, separate from transient tool retries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class TaskFailureCategory(StrEnum):
    BROWSER_GROUNDING = "BROWSER_GROUNDING"
    TASK_UNDERSTANDING = "TASK_UNDERSTANDING"
    PLAN_INCOMPLETE = "PLAN_INCOMPLETE"
    WRONG_TOOL = "WRONG_TOOL"
    TOOL_ARGUMENT = "TOOL_ARGUMENT"
    STALE_STATE = "STALE_STATE"
    NO_PROGRESS = "NO_PROGRESS"
    LOOP = "LOOP"
    PREMATURE_FINISH = "PREMATURE_FINISH"
    ARTIFACT_ERROR = "ARTIFACT_ERROR"
    CONSTRAINT_MISS = "CONSTRAINT_MISS"
    STATE_INCONSISTENCY = "STATE_INCONSISTENCY"
    UNKNOWN = "UNKNOWN"


class TaskRecoveryAction(StrEnum):
    REFRESH_STATE = "refresh_state"
    REPAIR_ARGUMENT = "repair_argument"
    SWITCH_TOOL = "switch_tool"
    VALIDATE_CONTRACT = "validate_contract"
    REPLAN = "replan"
    WRITE_PARTIAL = "write_partial"
    STOP_REPEATED_ACTION = "stop_repeated_action"
    STOP = "stop"


@dataclass(frozen=True)
class TaskRecoveryBudget:
    limits: Mapping[TaskRecoveryAction, int] = field(
        default_factory=lambda: {
            TaskRecoveryAction.REFRESH_STATE: 1,
            TaskRecoveryAction.REPAIR_ARGUMENT: 1,
            TaskRecoveryAction.SWITCH_TOOL: 1,
            TaskRecoveryAction.VALIDATE_CONTRACT: 1,
            TaskRecoveryAction.REPLAN: 1,
            TaskRecoveryAction.WRITE_PARTIAL: 1,
            TaskRecoveryAction.STOP_REPEATED_ACTION: 1,
        }
    )
    max_actions_per_decision: int = 3


@dataclass(frozen=True)
class TaskFailureContext:
    primary: TaskFailureCategory
    secondary: tuple[TaskFailureCategory, ...] = ()
    repeated_action_count: int = 0
    tool_error_count: int = 0
    attempts: Mapping[TaskRecoveryAction, int] = field(default_factory=dict)

    @property
    def categories(self) -> set[TaskFailureCategory]:
        return {self.primary, *self.secondary}


@dataclass(frozen=True)
class TaskRecoveryDecision:
    actions: tuple[TaskRecoveryAction, ...]
    should_continue: bool
    rationale: str


class TaskRecoveryPolicy(Protocol):
    def decide(self, context: TaskFailureContext) -> TaskRecoveryDecision: ...


class RuleBasedTaskRecoveryPolicy:
    """Choose bounded structural recovery; never retry semantic failures blindly."""

    def __init__(self, budget: TaskRecoveryBudget | None = None) -> None:
        self.budget = budget or TaskRecoveryBudget()

    def decide(self, context: TaskFailureContext) -> TaskRecoveryDecision:
        categories = context.categories
        proposed: list[TaskRecoveryAction] = []
        if context.primary is TaskFailureCategory.BROWSER_GROUNDING:
            if categories & {TaskFailureCategory.LOOP, TaskFailureCategory.NO_PROGRESS}:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
            proposed.extend((TaskRecoveryAction.REFRESH_STATE, TaskRecoveryAction.SWITCH_TOOL))
        elif context.primary is TaskFailureCategory.CONSTRAINT_MISS:
            proposed.append(TaskRecoveryAction.VALIDATE_CONTRACT)
            if categories & {
                TaskFailureCategory.TASK_UNDERSTANDING,
                TaskFailureCategory.STATE_INCONSISTENCY,
            }:
                proposed.append(TaskRecoveryAction.REPLAN)
        elif context.primary is TaskFailureCategory.WRONG_TOOL:
            proposed.append(TaskRecoveryAction.SWITCH_TOOL)
            if TaskFailureCategory.BROWSER_GROUNDING in categories:
                proposed.append(TaskRecoveryAction.REFRESH_STATE)
            proposed.append(TaskRecoveryAction.REPLAN)
        elif context.primary is TaskFailureCategory.PLAN_INCOMPLETE:
            proposed.append(TaskRecoveryAction.REPLAN)
            if TaskFailureCategory.TOOL_ARGUMENT in categories:
                proposed.append(TaskRecoveryAction.REPAIR_ARGUMENT)
            elif TaskFailureCategory.WRONG_TOOL in categories:
                proposed.append(TaskRecoveryAction.SWITCH_TOOL)
            proposed.append(TaskRecoveryAction.WRITE_PARTIAL)
        elif context.primary is TaskFailureCategory.ARTIFACT_ERROR:
            proposed.extend(
                (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.WRITE_PARTIAL)
            )
            if TaskFailureCategory.LOOP in categories:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
        else:
            proposed.append(TaskRecoveryAction.STOP)

        actions = tuple(
            action
            for action in dict.fromkeys(proposed)
            if context.attempts.get(action, 0) < self.budget.limits.get(action, 0)
        )[: self.budget.max_actions_per_decision]
        if not actions:
            return TaskRecoveryDecision(
                (TaskRecoveryAction.STOP,),
                False,
                "No safe task-level recovery remains within budget.",
            )
        return TaskRecoveryDecision(
            actions,
            True,
            "Apply bounded state/plan/contract recovery; low-level retries remain separate.",
        )


def parse_failure_categories(values: Sequence[str]) -> tuple[TaskFailureCategory, ...]:
    return tuple(
        TaskFailureCategory(value)
        if value in TaskFailureCategory._value2member_map_
        else TaskFailureCategory.UNKNOWN
        for value in values
    )
