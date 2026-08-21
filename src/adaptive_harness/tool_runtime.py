"""Guarded tool registry with pre/execute/post capability seams."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from adaptive_harness.capabilities import ToolCall, ToolDefinition, ToolResult
from adaptive_harness.tool_reliability import (
    BoundedRecoveryPolicy,
    ClassifiedToolFailure,
    FailureClassifier,
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    RuleBasedFailureClassifier,
    ToolReliabilityConfig,
)


class ToolPolicy(Protocol):
    async def before(self, call: ToolCall) -> ToolCall: ...

    async def after(self, call: ToolCall, result: ToolResult) -> ToolResult: ...


@dataclass(frozen=True)
class ToolAttempt:
    number: int
    call: ToolCall
    result: ToolResult
    failure: ClassifiedToolFailure | None = None
    recovery: RecoveryDecision | None = None


@dataclass(frozen=True)
class ToolExecutionTrace:
    call: ToolCall
    attempts: tuple[ToolAttempt, ...]

    @property
    def result(self) -> ToolResult:
        return self.attempts[-1].result


@dataclass
class ToolRuntime:
    tools: Sequence[ToolDefinition]
    policies: Sequence[ToolPolicy] = ()
    reliability: ToolReliabilityConfig = ToolReliabilityConfig()
    failure_classifier: FailureClassifier = RuleBasedFailureClassifier()
    recovery_policy: RecoveryPolicy = BoundedRecoveryPolicy()

    def __post_init__(self) -> None:
        self._by_name = {tool.name: tool for tool in self.tools}
        if len(self._by_name) != len(self.tools):
            raise ValueError("tool names must be unique")

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": item.name, "description": item.description} for item in self.tools]

    def fork(self, extra_tools: Sequence[ToolDefinition]) -> ToolRuntime:
        """Create a run-local registry with environment-provided tools."""

        return ToolRuntime(
            tools=[*self.tools, *extra_tools],
            policies=self.policies,
            reliability=self.reliability,
            failure_classifier=self.failure_classifier,
            recovery_policy=self.recovery_policy,
        )

    async def execute(self, call: ToolCall) -> ToolResult:
        return (await self.execute_with_trace(call)).result

    async def execute_with_trace(self, call: ToolCall) -> ToolExecutionTrace:
        attempts: list[ToolAttempt] = []
        max_attempts = self.reliability.max_attempts if self.reliability.enabled else 1
        for attempt in range(1, max_attempts + 1):
            current = call
            for policy in self.policies:
                current = await policy.before(current)
            result = await self._invoke(current)
            for policy in reversed(self.policies):
                result = await policy.after(current, result)

            failure = None
            recovery = None
            if self.reliability.enabled and result.error_type is not None:
                failure = self.failure_classifier.classify(current, result)
                recovery = self.recovery_policy.decide(
                    failure,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
            attempts.append(ToolAttempt(attempt, current, result, failure, recovery))
            if recovery is None or recovery.action is RecoveryAction.STOP:
                break
        return ToolExecutionTrace(call, tuple(attempts))

    async def _invoke(self, call: ToolCall) -> ToolResult:
        tool = self._by_name.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"Unknown tool: {call.name}", error_type="NOT_FOUND")
        try:
            value = tool.execute(**dict(call.arguments))
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, ToolResult):
                return ToolResult(call.id, value.content, value.error_type, value.metadata)
            return ToolResult(call.id, str(value))
        except Exception as exc:
            return ToolResult(call.id, str(exc), error_type=type(exc).__name__)
