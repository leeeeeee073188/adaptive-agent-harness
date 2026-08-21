"""Deterministic tool failure classification and bounded recovery decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from adaptive_harness.capabilities import ToolCall, ToolResult


class ToolFailureType(StrEnum):
    TIMEOUT = "TIMEOUT"
    TRANSIENT = "TRANSIENT"
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    AUTHORIZATION = "AUTHORIZATION"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class RecoveryAction(StrEnum):
    RETRY = "retry"
    STOP = "stop"


@dataclass(frozen=True)
class ClassifiedToolFailure:
    failure_type: ToolFailureType
    retryable: bool
    message: str


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str


@dataclass(frozen=True)
class ToolReliabilityConfig:
    enabled: bool = False
    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")


class FailureClassifier(Protocol):
    def classify(self, call: ToolCall, result: ToolResult) -> ClassifiedToolFailure: ...


class RecoveryPolicy(Protocol):
    def decide(
        self,
        failure: ClassifiedToolFailure,
        *,
        attempt: int,
        max_attempts: int,
    ) -> RecoveryDecision: ...


class RuleBasedFailureClassifier:
    """Classify normalized tool errors without another model call."""

    def classify(self, call: ToolCall, result: ToolResult) -> ClassifiedToolFailure:
        error = (result.error_type or "EXECUTION_ERROR").lower()
        message = result.content.lower()
        if "timeout" in error or "timed out" in message:
            return ClassifiedToolFailure(ToolFailureType.TIMEOUT, True, result.content)
        if error == "not_found" or "unknown tool" in message or "not found" in error:
            return ClassifiedToolFailure(ToolFailureType.NOT_FOUND, False, result.content)
        if error in {"typeerror", "valueerror", "invalid_argument", "schema_error"}:
            return ClassifiedToolFailure(ToolFailureType.INVALID_ARGUMENT, False, result.content)
        if any(marker in error for marker in ("permission", "authorization", "authentication")):
            return ClassifiedToolFailure(ToolFailureType.AUTHORIZATION, False, result.content)
        if any(marker in error or marker in message for marker in ("connection", "ratelimit", "429", "503")):
            return ClassifiedToolFailure(ToolFailureType.TRANSIENT, True, result.content)
        return ClassifiedToolFailure(ToolFailureType.EXECUTION_ERROR, False, result.content)


class BoundedRecoveryPolicy:
    """Retry only classified transient failures and never exceed the profile budget."""

    def decide(
        self,
        failure: ClassifiedToolFailure,
        *,
        attempt: int,
        max_attempts: int,
    ) -> RecoveryDecision:
        if failure.retryable and attempt < max_attempts:
            return RecoveryDecision(RecoveryAction.RETRY, "retryable failure within attempt budget")
        if failure.retryable:
            return RecoveryDecision(RecoveryAction.STOP, "retry budget exhausted")
        return RecoveryDecision(RecoveryAction.STOP, "failure is not safely retryable")
