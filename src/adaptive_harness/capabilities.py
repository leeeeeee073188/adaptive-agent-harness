"""Capability seams: definition, provider, and consumer-facing contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    error_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    execute: Any


class Environment(Protocol):
    async def build(self) -> None: ...

    async def cleanup(self) -> None: ...

    def state(self) -> Mapping[str, Any]: ...

    async def tools(self) -> Sequence[ToolDefinition]: ...


class Toolkit(Protocol):
    async def build(self, environment: Environment) -> None: ...

    async def cleanup(self) -> None: ...

    def tools(self) -> Sequence[ToolDefinition]: ...


@dataclass(frozen=True)
class ModelRequest:
    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]]
    context: Mapping[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)


class ModelAdapter(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ContextManager(Protocol):
    def prepare(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        environment_state: Mapping[str, Any],
        task_state: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class CompletionDecision:
    passed: bool
    feedback: str | None = None


class CompletionPolicy(Protocol):
    def check(self, task: str, response: ModelResponse, state: Mapping[str, Any]) -> CompletionDecision: ...


class PassthroughContextManager:
    def prepare(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        environment_state: Mapping[str, Any],
        task_state: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        if not environment_state and not task_state:
            return list(messages)
        snapshot = {"environment": dict(environment_state), "task": dict(task_state)}
        return [*messages, {"role": "user", "content": f"Current runtime snapshot: {snapshot}"}]


class AcceptFinalCompletion:
    def check(self, task: str, response: ModelResponse, state: Mapping[str, Any]) -> CompletionDecision:
        return CompletionDecision(
            passed=bool(response.content.strip()),
            feedback="A non-empty final response is required.",
        )
