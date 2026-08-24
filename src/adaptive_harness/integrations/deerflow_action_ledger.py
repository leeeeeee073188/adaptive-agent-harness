"""Optional DeerFlow tool middleware for ActionLedger audit/advice."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from adaptive_harness.action_audit import emit_action_audit
from adaptive_harness.action_ledger import (
    ToolActionLedger,
    ToolIntent,
    VerificationBudgetConfig,
    VerificationDisposition,
    VerificationMode,
    classify_tool_action,
)
from adaptive_harness.capabilities import ToolCall, ToolResult
from adaptive_harness.policy_session import current_policy_session
from adaptive_harness.resource_guardrail import NonMutatingTurnBudget

_MAX_RUN_LEDGERS = 128
_ADVICE = (
    "[HARNESS VERIFICATION BUDGET] Equivalent verification is over budget without a successful "
    "intervening mutation. Use existing evidence, deliver, or change strategy."
)


class DeerFlowToolCallLimitMiddleware(ToolCallLimitMiddleware):
    """Zero-argument, per-stream hard stop before excess tools execute."""

    def __init__(self) -> None:
        maximum = int(os.environ.get("ADAPTIVE_MAX_TOOL_CALLS_PER_TURN", "20"))
        if maximum < 1:
            raise ValueError("ADAPTIVE_MAX_TOOL_CALLS_PER_TURN must be positive")
        super().__init__(run_limit=maximum, exit_behavior="end")


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
        self._max_nonmutating_actions = int(
            os.environ.get("ADAPTIVE_MAX_NONMUTATING_ACTIONS_PER_TURN", "20")
        )
        if self._max_nonmutating_actions < 1:
            raise ValueError("ADAPTIVE_MAX_NONMUTATING_ACTIONS_PER_TURN must be positive")
        self._turn_budget = NonMutatingTurnBudget(self._max_nonmutating_actions)
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
        blocked = self._blocked_result(request)
        return self._observe(request, blocked if blocked is not None else handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        blocked = self._blocked_result(request)
        return self._observe(
            request,
            blocked if blocked is not None else await handler(request),
        )

    def _blocked_result(self, request: ToolCallRequest) -> ToolMessage | Command | None:
        session = current_policy_session()
        raw = request.tool_call
        call = ToolCall(
            str(raw.get("id") or "missing-id"),
            str(raw.get("name") or "unknown"),
            dict(raw.get("args") or {}),
        )
        semantics = classify_tool_action(call.name, call.arguments)
        if not self._turn_budget.admit(
            _run_key(request),
            mutating=semantics.mutating,
        ):
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=(
                                "Harness ended this turn after the non-mutating action budget "
                                "was exhausted. Replan around unsatisfied criteria next turn."
                            ),
                            tool_call_id=call.id,
                            status="error",
                        )
                    ]
                },
                goto=END,
            )
        if session is None or not session.is_action_blocked(call):
            threshold = (
                self._config.max_same_scope_reads
                if semantics.intent is ToolIntent.READ
                else self._config.max_same_scope
                if semantics.intent in {ToolIntent.OBSERVE, ToolIntent.VERIFY}
                else None
            )
            exhausted = bool(
                threshold is not None
                and any(
                    cluster.scope_key == semantics.scope_key
                    and cluster.attempts >= threshold
                    and cluster.repeated_unchanged
                    for cluster in self._ledger(request).clusters()
                )
            )
            if not exhausted:
                return None
        return ToolMessage(
            content="Harness blocked this repeated no-progress Action Scope.",
            tool_call_id=call.id,
            status="error",
        )


def _run_key(request: ToolCallRequest) -> str:
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    if isinstance(context, Mapping):
        for key in ("run_id", "thread_id"):
            if context.get(key):
                session = current_policy_session()
                turn = session.turn_index if session is not None else 0
                return f"{key}:{context[key]}:policy-turn:{turn}"
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
