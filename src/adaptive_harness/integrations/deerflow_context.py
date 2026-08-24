"""Optional DeerFlow model-call middleware for Task-aware Context selection.

This module is imported only inside a DeerFlow runtime that provides LangChain.
The core package intentionally keeps no LangChain dependency.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from adaptive_harness.context import (
    CONTEXT_POLICY_VERSION,
    ContextBudget,
    TaskAwareContextManager,
)
from adaptive_harness.context_audit import emit_context_audit

_AUTHORITY_MARKER = "HARNESS_CONTEXT_AUTHORITY"
_DATA_NAME = "harness-working-set-data"


class DeerFlowTaskAwareContextMiddleware(AgentMiddleware):
    """Apply the generic selector at DeerFlow's final model-request seam."""

    def __init__(self) -> None:
        super().__init__()
        budget = int(os.environ.get("ADAPTIVE_CONTEXT_MAX_INPUT_TOKENS", "4096"))
        self._manager = TaskAwareContextManager(
            budget=ContextBudget(max_input_tokens=budget),
        )
        self._snapshot_path = Path(
            os.environ.get("ADAPTIVE_CONTEXT_SNAPSHOT", "/task/.adaptive/context.json")
        )

    def _prepare(self, request: ModelRequest) -> ModelRequest:
        original = list(request.messages)
        mapped = [_message_to_mapping(message, index) for index, message in enumerate(original)]
        prepared = self._manager.prepare(
            mapped,
            environment_state={},
            task_state=self._task_state(request),
        )
        authority = ""
        selected: list[BaseMessage] = []
        for message in prepared.messages:
            source_index = message.get("_source_index")
            if isinstance(source_index, int):
                selected.append(original[source_index])
                continue
            content = str(message.get("content") or "")
            if message.get("role") == "system" and content.startswith(_AUTHORITY_MARKER):
                authority = content
                continue
            if message.get("name") == _DATA_NAME:
                selected.append(
                    HumanMessage(
                        content=content,
                        name=_DATA_NAME,
                        additional_kwargs={"hide_from_ui": True, "adaptive_context_data": True},
                    )
                )
        system_message = _merge_system_message(request.system_message, authority)
        emit_context_audit(
            {
                **dict(prepared.audit),
                "adapter": "deerflow-model-middleware-v1",
                "selected_message_count": len(selected),
            }
        )
        return request.override(messages=selected, system_message=system_message)

    def _task_state(self, request: ModelRequest) -> Mapping[str, Any]:
        snapshot: dict[str, Any] = {}
        if self._snapshot_path.is_file():
            document = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            if isinstance(document, Mapping):
                snapshot.update(document)
        state = request.state or {}
        live_values = {
            key: state.get(key)
            for key in ("summary_text", "todos", "delegations", "skill_context")
            if state.get(key)
        }
        task = snapshot.get("task")
        task_snapshot = dict(task) if isinstance(task, Mapping) else {}
        if live_values:
            task_snapshot["values"] = live_values
        return {"task": task_snapshot} if task_snapshot else {}

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        try:
            request = self._prepare(request)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            emit_context_audit(
                {
                    "policy": CONTEXT_POLICY_VERSION,
                    "adapter": "deerflow-model-middleware-v1",
                    "failed_open": True,
                    "error_type": type(error).__name__,
                }
            )
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        try:
            request = self._prepare(request)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            emit_context_audit(
                {
                    "policy": CONTEXT_POLICY_VERSION,
                    "adapter": "deerflow-model-middleware-v1",
                    "failed_open": True,
                    "error_type": type(error).__name__,
                }
            )
        return await handler(request)


def _message_to_mapping(message: BaseMessage, index: int) -> Mapping[str, Any]:
    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, ToolMessage):
        role = "tool"
    else:
        role = str(getattr(message, "type", ""))
    mapped: dict[str, Any] = {
        "role": role,
        "content": message.content,
        "_source_index": index,
    }
    if isinstance(message, AIMessage) and message.tool_calls:
        mapped["tool_calls"] = [
            {
                "id": str(call.get("id")),
                "name": str(call.get("name")),
                "arguments": dict(call.get("args") or {}),
            }
            for call in message.tool_calls
        ]
    if isinstance(message, ToolMessage):
        mapped["tool_call_id"] = str(message.tool_call_id)
    return mapped


def _merge_system_message(message: SystemMessage | None, authority: str) -> SystemMessage | None:
    if not authority:
        return message
    if message is None:
        return SystemMessage(content=authority)
    content = message.content if isinstance(message.content, str) else json.dumps(message.content)
    if _AUTHORITY_MARKER in content:
        return message
    return SystemMessage(
        content=f"{content}\n\n{authority}",
        additional_kwargs=dict(message.additional_kwargs),
        response_metadata=dict(message.response_metadata),
        name=message.name,
        id=message.id,
    )
