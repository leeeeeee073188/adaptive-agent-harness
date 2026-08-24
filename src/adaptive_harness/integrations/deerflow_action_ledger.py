"""Optional DeerFlow tool middleware for ActionLedger audit/advice."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from adaptive_harness.action_audit import emit_action_audit
from adaptive_harness.action_ledger import (
    ToolActionLedger,
    VerificationBudgetConfig,
    VerificationDisposition,
    VerificationMode,
)
from adaptive_harness.capabilities import ToolCall, ToolResult

_MAX_RUN_LEDGERS = 128
_ADVICE = (
    "[HARNESS VERIFICATION BUDGET] Equivalent verification is over budget without a successful "
    "intervening mutation. Use existing evidence, deliver, or change strategy."
)


class DeerFlowToolActionLedgerMiddleware(AgentMiddleware):
    """Observe every tool result; Advice mode is explicit and never blocks."""

    def __init__(self) -> None:
        super().__init__()
        mode = VerificationMode(os.environ.get("ADAPTIVE_VERIFICATION_MODE", "observe"))
        config = VerificationBudgetConfig(
            max_same_scope=int(os.environ.get("ADAPTIVE_VERIFY_MAX_SAME_SCOPE", "2")),
            max_total_since_mutation=int(os.environ.get("ADAPTIVE_VERIFY_MAX_TOTAL", "4")),
            mode=mode,
        )
        self._config = config
        self._ledgers: OrderedDict[str, ToolActionLedger] = OrderedDict()

    def _ledger(self, request: ToolCallRequest) -> ToolActionLedger:
        key = _run_key(request)
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = ToolActionLedger(self._config)
            self._ledgers[key] = ledger
            if len(self._ledgers) > _MAX_RUN_LEDGERS:
                self._ledgers.popitem(last=False)
        else:
            self._ledgers.move_to_end(key)
        return ledger

    def _observe(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        raw = request.tool_call
        call = ToolCall(
            str(raw.get("id") or "missing-id"),
            str(raw.get("name") or "unknown"),
            dict(raw.get("args") or {}),
        )
        tool_result = _tool_result(call.id, result)
        record, decision = self._ledger(request).observe(call, tool_result)
        advice_applied = bool(
            self._config.mode is VerificationMode.ADVISE
            and decision.disposition is VerificationDisposition.WARN
            and isinstance(result, ToolMessage)
        )
        emit_action_audit(
            {
                "policy": "tool-action-ledger-v1",
                "mode": self._config.mode.value,
                "record": record.to_payload(),
                "decision": decision.to_payload(),
                "advice_applied": advice_applied,
            }
        )
        if advice_applied:
            content = _content_text(result.content)
            return result.model_copy(update={"content": f"{content}\n\n{_ADVICE}"})
        return result

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return self._observe(request, handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return self._observe(request, await handler(request))


def _run_key(request: ToolCallRequest) -> str:
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        for key in ("run_id", "thread_id"):
            if context.get(key):
                return f"{key}:{context[key]}"
    return f"runtime:{id(runtime)}"


def _tool_result(call_id: str, result: ToolMessage | Command) -> ToolResult:
    if isinstance(result, ToolMessage):
        error_type = "TOOL_ERROR" if getattr(result, "status", None) == "error" else None
        return ToolResult(call_id, _content_text(result.content), error_type=error_type)
    update = getattr(result, "update", None)
    return ToolResult(
        call_id,
        json.dumps(update if update is not None else str(result), ensure_ascii=False, default=str),
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)

