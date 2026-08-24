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
from adaptive_harness.policy_session import (
    KernelPolicySession,
    current_policy_session,
    current_policy_task_state,
)
from adaptive_harness.redaction import deep_redact, scrub_text

_AUTHORITY_MARKER = "HARNESS_CONTEXT_AUTHORITY"
_DATA_NAME = "harness-working-set-data"


class DeerFlowTaskAwareContextMiddleware(AgentMiddleware):
    """Apply the generic selector at DeerFlow's final model-request seam."""

    def __init__(self) -> None:
        super().__init__()
        budget = int(os.environ.get("ADAPTIVE_CONTEXT_MAX_INPUT_TOKENS", "4096"))
        self._session = KernelPolicySession(
            context_manager=TaskAwareContextManager(
                budget=ContextBudget(max_input_tokens=budget),
            )
        )
        self._snapshot_path = Path(
            os.environ.get("ADAPTIVE_CONTEXT_SNAPSHOT", "/task/.adaptive/context.json")
        )

    def _prepare(self, request: ModelRequest) -> ModelRequest:
        original = list(request.messages)
        mapped = [_message_to_mapping(message, index) for index, message in enumerate(original)]
        bound_session = current_policy_session()
        session = (
            bound_session
            if bound_session is not None and bound_session.context_manager is not None
            else self._session
        )
        prepared = session.prepare_context(
            mapped,
            environment_state={},
            task_state=self._task_state(request),
        )
        authority = ""
        selected: list[BaseMessage] = []
        for message in prepared.messages:
            content = str(message.get("content") or "")
            if message.get("role") == "system" and content.startswith(_AUTHORITY_MARKER):
                authority = content
                continue
            converted = _mapping_to_message(message)
            if converted is not None:
                selected.append(converted)
        system_message = _merge_system_message(request.system_message, authority)
        emit_context_audit(
            {
                **dict(prepared.audit),
                "adapter": "deerflow-model-middleware-v1",
                "profile_session_bound": bound_session is not None,
                "selected_message_count": len(selected),
            }
        )
        return request.override(messages=selected, system_message=system_message)

    def _task_state(self, request: ModelRequest) -> Mapping[str, Any]:
        snapshot: dict[str, Any] = {}
        if self._snapshot_path.is_file():
            document = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
            if isinstance(document, Mapping):
                snapshot.update(_mutable_context_copy(document))
        bound_task_state = current_policy_task_state()
        if bound_task_state is not None:
            snapshot.update(_mutable_context_copy(bound_task_state))
        state = request.state or {}
        live_values = {
            key: state.get(key)
            for key in ("summary_text", "todos", "delegations", "skill_context")
            if state.get(key)
        }
        task = snapshot.get("task")
        task_snapshot = _mutable_context_copy(task) if isinstance(task, Mapping) else {}
        if live_values:
            values = task_snapshot.get("values")
            task_snapshot["values"] = {
                **(values if isinstance(values, Mapping) else {}),
                **live_values,
            }
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
            request = _safe_minimal_request(request)
            emit_context_audit(
                {
                    "policy": CONTEXT_POLICY_VERSION,
                    "adapter": "deerflow-model-middleware-v1",
                    "safe_fallback": True,
                    "dropped_tool_history": True,
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
            request = _safe_minimal_request(request)
            emit_context_audit(
                {
                    "policy": CONTEXT_POLICY_VERSION,
                    "adapter": "deerflow-model-middleware-v1",
                    "safe_fallback": True,
                    "dropped_tool_history": True,
                    "error_type": type(error).__name__,
                }
            )
        return await handler(request)


def _mapping_to_message(message: Mapping[str, Any]) -> BaseMessage | None:
    role = str(message.get("role") or "")
    content = message.get("content", "")
    name = message.get("name")
    if role == "system":
        return SystemMessage(content=content, name=name)
    if role == "user":
        kwargs = {"name": name} if name else {}
        if name == _DATA_NAME:
            kwargs["additional_kwargs"] = {"hide_from_ui": True, "adaptive_context_data": True}
        return HumanMessage(content=content, **kwargs)
    if role == "assistant":
        return AIMessage(content=content, tool_calls=_langchain_tool_calls(message.get("tool_calls") or ()))
    if role == "tool":
        return ToolMessage(content=content, tool_call_id=str(message.get("tool_call_id") or ""))
    return None


def _langchain_tool_calls(raw_calls: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(raw_calls, list | tuple):
        return calls
    for raw in raw_calls:
        if not isinstance(raw, Mapping):
            continue
        call_id = str(raw.get("id") or "")
        name = str(raw.get("name") or "")
        args = raw.get("args")
        if isinstance(raw.get("function"), Mapping):
            function = raw["function"]
            name = str(function.get("name") or name)
            args = function.get("arguments", args)
        if args is None:
            args = raw.get("arguments") or {}
        if isinstance(args, str):
            try:
                parsed_args = json.loads(args)
            except json.JSONDecodeError:
                parsed_args = {"raw_arguments": args}
            args = parsed_args if isinstance(parsed_args, Mapping) else {"raw_arguments": args}
        calls.append(
            {
                "id": call_id,
                "name": name or "unknown",
                "args": dict(args) if isinstance(args, Mapping) else {},
            }
        )
    return calls


def _safe_minimal_request(request: ModelRequest) -> ModelRequest:
    candidates = [
        message
        for message in request.messages
        if isinstance(message, (HumanMessage, SystemMessage))
    ]
    selected: list[BaseMessage] = []
    for message in _first_and_latest(candidates):
        content = message.content if isinstance(message.content, str) else json.dumps(deep_redact(message.content))
        safe_content = scrub_text(content, drop_sensitive_lines=False)
        if isinstance(message, SystemMessage):
            selected.append(SystemMessage(content=safe_content, name=message.name))
        else:
            selected.append(HumanMessage(content=safe_content, name=message.name))
    system_message = request.system_message
    if system_message is not None:
        if isinstance(system_message.content, str):
            content = system_message.content
        else:
            content = json.dumps(deep_redact(system_message.content))
        system_message = SystemMessage(
            content=scrub_text(content, drop_sensitive_lines=False),
            name=system_message.name,
        )
    return request.override(messages=selected, system_message=system_message)


def _first_and_latest(messages: list[BaseMessage]) -> list[BaseMessage]:
    selected: list[BaseMessage] = []
    first_system = next((message for message in messages if isinstance(message, SystemMessage)), None)
    first_human = next((message for message in messages if isinstance(message, HumanMessage)), None)
    latest_human_or_system = messages[-1] if messages else None
    for message in (first_system, first_human, latest_human_or_system):
        if message is not None and message not in selected:
            selected.append(message)
    return selected


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


def _mutable_context_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_context_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_context_copy(item) for item in value]
    return value
