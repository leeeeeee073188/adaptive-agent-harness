"""Guarded tool registry with pre/execute/post capability seams."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from adaptive_harness.capabilities import ToolCall, ToolDefinition, ToolResult


class ToolPolicy(Protocol):
    async def before(self, call: ToolCall) -> ToolCall: ...

    async def after(self, call: ToolCall, result: ToolResult) -> ToolResult: ...


@dataclass
class ToolRuntime:
    tools: Sequence[ToolDefinition]
    policies: Sequence[ToolPolicy] = ()

    def __post_init__(self) -> None:
        self._by_name = {tool.name: tool for tool in self.tools}
        if len(self._by_name) != len(self.tools):
            raise ValueError("tool names must be unique")

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": item.name, "description": item.description} for item in self.tools]

    def fork(self, extra_tools: Sequence[ToolDefinition]) -> ToolRuntime:
        """Create a run-local registry with environment-provided tools."""

        return ToolRuntime([*self.tools, *extra_tools], self.policies)

    async def execute(self, call: ToolCall) -> ToolResult:
        current = call
        for policy in self.policies:
            current = await policy.before(current)
        tool = self._by_name.get(current.name)
        if tool is None:
            result = ToolResult(current.id, f"Unknown tool: {current.name}", error_type="NOT_FOUND")
        else:
            try:
                value = tool.execute(**dict(current.arguments))
                if inspect.isawaitable(value):
                    value = await value
                result = ToolResult(current.id, str(value))
            except Exception as exc:
                result = ToolResult(current.id, str(exc), error_type=type(exc).__name__)
        for policy in reversed(self.policies):
            result = await policy.after(current, result)
        return result
