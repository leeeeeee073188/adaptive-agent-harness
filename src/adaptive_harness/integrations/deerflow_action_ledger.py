"""Optional DeerFlow tool middleware for ActionLedger audit/advice."""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
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
        self._max_reads_per_resource = int(os.environ.get("ADAPTIVE_MAX_READS_PER_RESOURCE", "3"))
        if self._max_reads_per_resource < 1:
            raise ValueError("ADAPTIVE_MAX_READS_PER_RESOURCE must be positive")
        self._turn_budget = NonMutatingTurnBudget(self._max_nonmutating_actions)
        self._delivery_satisfied: set[str] = set()
        self._read_counts: dict[str, dict[str, int]] = {}
        self._read_inflight: dict[str, dict[str, tuple[tuple[str, ...], int]]] = {}
        self._resource_epochs: dict[str, int] = {}
        self._local_cache_blocks: dict[str, int] = {}
        self._ledgers: OrderedDict[str, ToolActionLedger] = OrderedDict()
        self._state_lock = Lock()

    def _ledger(self, request: ToolCallRequest) -> ToolActionLedger:
        key = _run_key(request)
        with self._state_lock:
            return self._ledger_locked(key)

    def _ledger_locked(self, key: str) -> ToolActionLedger:
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = ToolActionLedger(self._config)
            self._ledgers[key] = ledger
            if len(self._ledgers) > _MAX_RUN_LEDGERS:
                evicted_key, _ = self._ledgers.popitem(last=False)
                self._delivery_satisfied.discard(evicted_key)
                self._read_counts.pop(evicted_key, None)
                self._read_inflight.pop(evicted_key, None)
                self._resource_epochs.pop(evicted_key, None)
                self._discard_turn_state_locked(evicted_key)
        else:
            self._ledgers.move_to_end(key)
        return ledger

    def _reserve_read_resources_locked(
        self,
        run_key: str,
        call_id: str,
        resources: tuple[str, ...],
    ) -> bool:
        counts = self._read_counts.setdefault(run_key, {})
        inflight = self._read_inflight.setdefault(run_key, {})
        reservations: dict[str, int] = {}
        for reserved_resources, _epoch in inflight.values():
            for resource in reserved_resources:
                reservations[resource] = reservations.get(resource, 0) + 1
        exhausted = all(
            counts.get(resource, 0) + reservations.get(resource, 0) >= self._max_reads_per_resource
            for resource in resources
        )
        if exhausted:
            return False
        inflight[call_id] = (resources, self._resource_epochs.get(run_key, 0))
        return True

    def _release_read_reservation_locked(
        self,
        run_key: str,
        call_id: str,
    ) -> tuple[tuple[str, ...], int] | None:
        inflight = self._read_inflight.get(run_key)
        if inflight is None:
            return None
        reserved = inflight.pop(call_id, None)
        if not inflight:
            self._read_inflight.pop(run_key, None)
        return reserved

    def _record_local_cache_block_locked(self, turn_key: str) -> int:
        count = self._local_cache_blocks.get(turn_key, 0) + 1
        self._local_cache_blocks[turn_key] = count
        return count

    def _discard_turn_state_locked(self, run_key: str) -> None:
        prefix = f"{run_key}:policy-turn:"
        for key in tuple(self._local_cache_blocks):
            if key.startswith(prefix):
                self._local_cache_blocks.pop(key, None)

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
        run_key = _run_key(request)
        semantics = classify_tool_action(call.name, call.arguments)
        with self._state_lock:
            ledger = self._ledger_locked(run_key)
            record, decision = ledger.observe(call, tool_result)
            reserved = self._release_read_reservation_locked(run_key, call.id)
            if tool_result.error_type is None and semantics.mutating:
                self._read_counts.pop(run_key, None)
                self._resource_epochs[run_key] = self._resource_epochs.get(run_key, 0) + 1
            elif tool_result.error_type is None and reserved is not None:
                resources, epoch = reserved
                if epoch == self._resource_epochs.get(run_key, 0):
                    counts = self._read_counts.setdefault(run_key, {})
                    for resource in resources:
                        counts[resource] = counts.get(resource, 0) + 1
            if tool_result.error_type is None and _is_delivery_write(call):
                self._delivery_satisfied.add(run_key)
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
        run_key = _run_key(request)
        with self._state_lock:
            self._ledger_locked(run_key)
            delivery_satisfied = run_key in self._delivery_satisfied
        delivery_required = _delivery_required(request.state) and not delivery_satisfied
        if delivery_required and not _advances_delivery(call, semantics):
            blocked_kind = "non-output write" if semantics.mutating else "plain read"
            return ToolMessage(
                content=(
                    f"[HARNESS DELIVERY REQUIRED] {blocked_kind} blocked. Write a required artifact or "
                    "run a direct task-provided synthesis script. Do not create an empty placeholder "
                    "solely to unlock inspection."
                ),
                tool_call_id=call.id,
                status="error",
            )
        if not self._turn_budget.admit(
            _turn_key(request),
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
        local_resources = _cacheable_local_resources(semantics)
        if local_resources:
            with self._state_lock:
                budget_exhausted = not self._reserve_read_resources_locked(
                    run_key,
                    call.id,
                    local_resources,
                )
            if budget_exhausted:
                return self._local_cache_block_result(call.id, _turn_key(request))
        if session is None or not session.is_action_blocked(call):
            threshold = (
                self._config.max_same_scope_reads
                if semantics.intent is ToolIntent.READ
                else self._config.max_same_scope
                if semantics.intent in {ToolIntent.OBSERVE, ToolIntent.VERIFY}
                else None
            )
            with self._state_lock:
                clusters = self._ledger_locked(run_key).clusters()
            exhausted = bool(
                threshold is not None
                and any(
                    cluster.scope_key == semantics.scope_key
                    and cluster.attempts >= threshold
                    and cluster.repeated_unchanged
                    for cluster in clusters
                )
            )
            if not exhausted:
                return None
            if local_resources:
                with self._state_lock:
                    self._release_read_reservation_locked(run_key, call.id)
        if local_resources:
            with self._state_lock:
                self._release_read_reservation_locked(run_key, call.id)
        return ToolMessage(
            content="Harness blocked this repeated no-progress Action Scope.",
            tool_call_id=call.id,
            status="error",
        )

    def _local_cache_block_result(self, call_id: str, turn_key: str) -> ToolMessage | Command:
        content = (
            "[HARNESS LOCAL RESOURCE CACHE REQUIRED] Read-only access blocked because "
            "all local resources in this call already reached the task-run read budget. "
            "Use the Visible Evidence Workspace, deliver from existing evidence, or change strategy."
        )
        with self._state_lock:
            count = self._record_local_cache_block_locked(turn_key)
        if count < 2:
            return ToolMessage(content=content, tool_call_id=call_id, status="error")
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"{content} Ending this model loop so Policy recovery can replan.",
                        tool_call_id=call_id,
                        status="error",
                    )
                ]
            },
            goto=END,
        )


def _run_key(request: ToolCallRequest) -> str:
    """Return the stable task-run key shared by all policy turns in one run."""

    session = current_policy_session()
    if session is not None and session.run_id:
        return f"policy-session:{session.run_id}"
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    for key in ("run_id", "thread_id"):
        value = _context_value(context, key)
        if value:
            return f"{key}:{value}"
    return f"runtime:{id(runtime)}"


def _turn_key(request: ToolCallRequest) -> str:
    """Return the per-policy-turn key for budgets that intentionally reset each turn."""

    run_key = _run_key(request)
    session = current_policy_session()
    if session is not None:
        return f"{run_key}:policy-turn:{session.turn_index}"
    runtime = getattr(request, "runtime", None)
    context = getattr(runtime, "context", None)
    for key in ("policy_turn", "turn_index", "turn"):
        value = _context_value(context, key)
        if value is not None:
            return f"{run_key}:policy-turn:{value}"
    return f"{run_key}:policy-turn:0"


def _context_value(context: Any, key: str) -> Any:
    if isinstance(context, Mapping):
        return context.get(key)
    return getattr(context, key, None)


def _tool_result(call_id: str, result: ToolMessage | Command) -> ToolResult:
    if isinstance(result, ToolMessage):
        content = _content_text(result.content)
        error_type = (
            "TOOL_ERROR"
            if getattr(result, "status", None) == "error" or _looks_like_textual_tool_error(content)
            else None
        )
        return ToolResult(call_id, content, error_type=error_type)
    update = getattr(result, "update", None)
    error_type = "TOOL_ERROR" if _command_contains_error_message(update) else None
    return ToolResult(
        call_id,
        json.dumps(update if update is not None else str(result), ensure_ascii=False, default=str),
        error_type=error_type,
    )


def _command_contains_error_message(update: Any) -> bool:
    if not isinstance(update, Mapping):
        return False
    messages = update.get("messages")
    if not isinstance(messages, list):
        return False
    return any(getattr(message, "status", None) == "error" for message in messages)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


_TEXTUAL_TOOL_ERROR = re.compile(
    r"^\s*(?:Error:|Traceback\s+\(most\s+recent\s+call\s+last\):|Permission\s+denied\b|"
    r"(?:bash|sh):\s+[^\n]+:\s+(?:command\s+not\s+found|No\s+such\s+file\s+or\s+directory)\b)",
    re.IGNORECASE,
)


def _looks_like_textual_tool_error(content: str) -> bool:
    """Recognize stable tool-runtime envelopes, not arbitrary task prose mentioning errors."""

    return bool(_TEXTUAL_TOOL_ERROR.search(content))


def _delivery_required(state: Any) -> bool:
    if not isinstance(state, Mapping):
        return False
    messages = state.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        "[HARNESS DELIVERY REQUIRED]" in _content_text(getattr(message, "content", ""))
        for message in messages
    )


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


def _advances_delivery(call: ToolCall, semantics: Any) -> bool:
    if _is_delivery_write(call):
        return True
    return semantics.intent in {
        ToolIntent.NAVIGATE,
        ToolIntent.TRANSFORM,
        ToolIntent.INTERACT,
    }


def _cacheable_local_resources(semantics) -> tuple[str, ...]:
    if semantics.intent is not ToolIntent.READ:
        return ()
    return tuple(
        resource
        for resource in semantics.resources
        if not resource.startswith(("http://", "https://")) and not re.search(r"[*?{}\[\]]", resource)
    )
