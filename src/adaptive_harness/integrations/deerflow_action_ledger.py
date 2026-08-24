"""Optional DeerFlow tool middleware for ActionLedger audit/advice."""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from threading import Lock
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
        self._delivery_satisfied: set[str] = set()
        self._delivery_lock = Lock()
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
        if tool_result.error_type is None and _is_delivery_write(call):
            with self._delivery_lock:
                self._delivery_satisfied.add(_run_key(request))
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
        ledger = self._ledger(request)
        with self._delivery_lock:
            delivery_satisfied = _run_key(request) in self._delivery_satisfied
        delivery_paths = _delivery_paths(request.state)
        delivery_artifact_exists = any(_safe_task_output_exists(path) for path in delivery_paths)
        delivery_required = bool(delivery_paths) and not (
            delivery_satisfied or delivery_artifact_exists
        )
        if delivery_required and not semantics.mutating:
            return ToolMessage(
                content=(
                    "[HARNESS DELIVERY REQUIRED] Read-only action blocked. Write a required "
                    "artifact before further inspection."
                ),
                tool_call_id=call.id,
                status="error",
            )
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
                    for cluster in ledger.clusters()
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


def _delivery_paths(state: Any) -> tuple[str, ...]:
    if not isinstance(state, Mapping):
        return ()
    messages = state.get("messages")
    if not isinstance(messages, list):
        return ()
    paths = []
    for message in messages:
        content = _content_text(getattr(message, "content", ""))
        if "[HARNESS DELIVERY REQUIRED]" not in content:
            continue
        paths.extend(
            match.removeprefix("/task/")
            for match in re.findall(
                r"(?:/task/)?outputs/[A-Za-z0-9_./-]*[A-Za-z0-9_/-]",
                content,
            )
        )
    return tuple(dict.fromkeys(paths))


def _safe_task_output_exists(relative: str) -> bool:
    path = (Path("/task") / relative).resolve()
    output_root = Path("/task/outputs").resolve()
    return path.is_relative_to(output_root) and path.is_file()


def _is_delivery_write(call: ToolCall) -> bool:
    path = str(call.arguments.get("path") or "").replace("\\", "/")
    output_path = path.startswith(("/task/outputs/", "outputs/"))
    if call.name in {"write_file", "str_replace"}:
        return output_path
    if call.name != "bash":
        return False
    command = str(call.arguments.get("command") or "")
    if not re.search(r"(?:/task/outputs/|\boutputs/)", command):
        return False
    return bool(
        re.search(
            r"(?:>{1,2}|\btee\b|\bcp\b|\bmv\b|write_text|write_bytes|"
            r"json\.dump|to_csv|open\s*\([^\n]{0,200}['\"](?:w|a|x))",
            command,
            re.IGNORECASE,
        )
    )
