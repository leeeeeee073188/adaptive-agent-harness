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
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    SYNTHESIS_LINEAGE_GAP = "SYNTHESIS_LINEAGE_GAP"
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
    REPAIR_ARTIFACT = "repair_artifact"
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
            TaskRecoveryAction.REPAIR_ARTIFACT: 1,
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


@dataclass(frozen=True)
class RecoveryActionEffect:
    action: TaskRecoveryAction
    status: str
    state_delta: Mapping[str, object]
    directive: str

    def to_payload(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "status": self.status,
            "state_delta": dict(self.state_delta),
            "directive": self.directive,
        }


@dataclass(frozen=True)
class RecoveryExecution:
    effects: tuple[RecoveryActionEffect, ...]

    @property
    def state_delta(self) -> dict[str, object]:
        combined: dict[str, object] = {}
        for effect in self.effects:
            combined.update(effect.state_delta)
        return combined

    @property
    def directives(self) -> tuple[str, ...]:
        return tuple(effect.directive for effect in self.effects if effect.directive)


@dataclass(frozen=True)
class RecoveryOutcome:
    execution_seq: int
    actions: tuple[TaskRecoveryAction, ...]
    progress_status: str
    completion_passed: bool
    effective: bool
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "execution_seq": self.execution_seq,
            "actions": [action.value for action in self.actions],
            "progress_status": self.progress_status,
            "completion_passed": self.completion_passed,
            "effective": self.effective,
            "reason": self.reason,
        }


class TaskRecoveryPolicy(Protocol):
    def decide(self, context: TaskFailureContext) -> TaskRecoveryDecision: ...


class TaskRecoveryExecutor(Protocol):
    def execute(
        self,
        decision: TaskRecoveryDecision,
        *,
        missing: Sequence[str] = (),
    ) -> RecoveryExecution: ...


class RecoveryOutcomeEvaluator(Protocol):
    def evaluate(
        self,
        *,
        execution_seq: int,
        execution: RecoveryExecution,
        progress_status: str,
        completion_passed: bool,
    ) -> RecoveryOutcome: ...


class RuleBasedTaskRecoveryPolicy:
    """Choose bounded structural recovery; never retry semantic failures blindly."""

    def __init__(self, budget: TaskRecoveryBudget | None = None) -> None:
        self.budget = budget or TaskRecoveryBudget()

    def decide(self, context: TaskFailureContext) -> TaskRecoveryDecision:
        categories = context.categories
        proposed: list[TaskRecoveryAction] = []
        if context.repeated_action_count >= 3:
            proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
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
        elif context.primary is TaskFailureCategory.EVIDENCE_GAP:
            if TaskFailureCategory.LOOP in categories:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
            proposed.extend((TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN))
        elif context.primary is TaskFailureCategory.SYNTHESIS_LINEAGE_GAP:
            if TaskFailureCategory.LOOP in categories:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
            proposed.extend((TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPLAN))
        elif context.primary is TaskFailureCategory.ARTIFACT_ERROR:
            proposed.extend(
                (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.WRITE_PARTIAL)
            )
            if TaskFailureCategory.LOOP in categories:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
        elif context.primary is TaskFailureCategory.ARTIFACT_INVALID:
            proposed.extend(
                (TaskRecoveryAction.VALIDATE_CONTRACT, TaskRecoveryAction.REPAIR_ARTIFACT)
            )
            if TaskFailureCategory.LOOP in categories:
                proposed.append(TaskRecoveryAction.STOP_REPEATED_ACTION)
        elif context.primary in {
            TaskFailureCategory.STALE_STATE,
            TaskFailureCategory.STATE_INCONSISTENCY,
        }:
            proposed.extend((TaskRecoveryAction.REFRESH_STATE, TaskRecoveryAction.REPLAN))
        elif context.primary is TaskFailureCategory.PREMATURE_FINISH:
            proposed.append(TaskRecoveryAction.VALIDATE_CONTRACT)
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


class RuleBasedTaskRecoveryExecutor:
    """Apply Harness control effects; external work remains a next-turn tool action."""

    def execute(
        self,
        decision: TaskRecoveryDecision,
        *,
        missing: Sequence[str] = (),
    ) -> RecoveryExecution:
        effects = tuple(self._effect(action, missing) for action in decision.actions)
        return RecoveryExecution(effects)

    def _effect(
        self,
        action: TaskRecoveryAction,
        missing: Sequence[str],
    ) -> RecoveryActionEffect:
        missing_text = "; ".join(missing) if missing else "a required task artifact"
        mapping: dict[TaskRecoveryAction, tuple[dict[str, object], str]] = {
            TaskRecoveryAction.REFRESH_STATE: (
                {"recovery.refresh_state_requested": True},
                "Refresh observable environment state; discard stale selectors and snapshots.",
            ),
            TaskRecoveryAction.REPAIR_ARGUMENT: (
                {"recovery.argument_repair_requested": True},
                "Repair tool arguments from the public schema before one bounded retry.",
            ),
            TaskRecoveryAction.SWITCH_TOOL: (
                {"recovery.tool_strategy": "first_class_alternative"},
                "Switch from the failed fallback to a first-class task tool.",
            ),
            TaskRecoveryAction.VALIDATE_CONTRACT: (
                {"recovery.missing_requirements": list(missing)},
                "Validate each missing contract criterion against fresh evidence.",
            ),
            TaskRecoveryAction.REPLAN: (
                {"recovery.replan_requested": True},
                "Replan only the remaining unsatisfied criteria.",
            ),
            TaskRecoveryAction.WRITE_PARTIAL: (
                {"recovery.partial_delivery_requested": True},
                (
                    "[HARNESS DELIVERY REQUIRED] The next successful tool action must write or "
                    f"directly synthesize {missing_text}. Run a task-provided transform script when "
                    "available; do not perform more plain reads. Never create a knowingly empty "
                    "placeholder solely to unlock inspection."
                ),
            ),
            TaskRecoveryAction.REPAIR_ARTIFACT: (
                {"recovery.partial_delivery_requested": True},
                (
                    "[HARNESS DELIVERY REQUIRED] The existing artifact failed public "
                    f"validation. Repair or directly re-synthesize {missing_text}; do not "
                    "resume plain reads or create another knowingly invalid placeholder."
                ),
            ),
            TaskRecoveryAction.STOP_REPEATED_ACTION: (
                {"recovery.repeated_action_blocked": True},
                "Do not repeat the previous no-progress action signature.",
            ),
            TaskRecoveryAction.STOP: (
                {"recovery.stop_requested": True},
                "Stop: no safe recovery remains within budget.",
            ),
        }
        delta, directive = mapping[action]
        return RecoveryActionEffect(action, "applied", delta, directive)


class RuleBasedRecoveryOutcomeEvaluator:
    """Credit recovery only for semantic progress or completed criteria."""

    def evaluate(
        self,
        *,
        execution_seq: int,
        execution: RecoveryExecution,
        progress_status: str,
        completion_passed: bool,
    ) -> RecoveryOutcome:
        effective = completion_passed or progress_status == "progressed"
        reason = (
            "Recovery was followed by semantic task progress or completion."
            if effective
            else "Recovery produced no semantic task progress."
        )
        return RecoveryOutcome(
            execution_seq,
            tuple(effect.action for effect in execution.effects),
            progress_status,
            completion_passed,
            effective,
            reason,
        )


def parse_failure_categories(values: Sequence[str]) -> tuple[TaskFailureCategory, ...]:
    return tuple(
        TaskFailureCategory(value)
        if value in TaskFailureCategory._value2member_map_
        else TaskFailureCategory.UNKNOWN
        for value in values
    )
