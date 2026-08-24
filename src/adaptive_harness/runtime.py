"""Turn/step driver whose inputs and outputs are fully reconstructable."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.action_ledger import ToolActionLedger, classify_tool_action
from adaptive_harness.capabilities import (
    CompletionDecision,
    ModelRequest,
    PreparedContext,
    ToolResult,
)
from adaptive_harness.context import CONTEXT_SELECTED
from adaptive_harness.kernel import Kernel
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.lifecycle import AGENT_PRE_STEP, AGENT_REQUEST, AGENT_TURN_STOPPING, RUN_STARTED, RUN_STOPPED
from adaptive_harness.policy_session import KernelPolicySession
from adaptive_harness.progress import RuleBasedProgressDetector
from adaptive_harness.resource_guardrail import NonMutatingTurnBudget, ResourceGuardrail
from adaptive_harness.runtime_contract import (
    RuntimeRequest,
    RuntimeResult,
    reject_runtime_credentials,
)
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    ENVIRONMENT,
    MODEL,
    PROGRESS_DETECTOR,
    RECOVERY_OUTCOME_EVALUATOR,
    RESOURCE_GUARDRAIL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
    TASK_RECOVERY_EXECUTOR,
    TASK_RECOVERY_POLICY,
    TOOL_RUNTIME,
)
from adaptive_harness.task_state import (
    EvidenceSource,
    Failure,
    TaskEventWriter,
    TaskStateProjector,
    evidence_from_tool_result,
)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    content: str
    completed: bool
    steps: int
    ledger: SessionLedger


class AgentDriver:
    """One lead-agent turn with zero or more model/tool steps."""

    def __init__(
        self,
        kernel: Kernel,
        *,
        max_steps: int = 64,
        allow_unverified_completion: bool = False,
    ) -> None:
        self.kernel = kernel
        self.max_steps = max_steps
        self.allow_unverified_completion = allow_unverified_completion

    async def run(
        self,
        task: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        public_schema: Mapping[str, object] | None = None,
        ledger_path: Path | None = None,
    ) -> RunResult:
        run_id = run_id or str(uuid.uuid4())
        ledger = SessionLedger(run_id, ledger_path)
        session = _build_policy_session(
            self.kernel,
            allow_unverified_completion=self.allow_unverified_completion,
        )
        action_ledger = ToolActionLedger()
        turn_budget = NonMutatingTurnBudget(20)
        environment = self.kernel.services.get(ENVIRONMENT)
        model = self.kernel.services.get(MODEL)
        tool_runtime = self.kernel.services.get(TOOL_RUNTIME)
        cleanup_required = False
        run_started = False
        try:
            ledger.append("runtime/start", {"runtime": "agent-driver"})
            cleanup_required = True
            await environment.build()
            tool_runtime = tool_runtime.fork(await environment.tools())
            await self.kernel.events.dispatch(RUN_STARTED, {"run_id": run_id, "task": task})
            run_started = True
            session.start_contract(
                ledger,
                task_id=task_id or run_id,
                task_prompt=task,
                public_schema=public_schema,
            )
            ledger.append("turn/start", {"trigger": "task"}, turn=1)
            ledger.append("user/message", {"content": task, "source": "task"}, turn=1)
            for step in range(1, self.max_steps + 1):
                ledger.append("step/start", {}, turn=1, step=step)
                task_projection = TaskStateProjector().project(ledger.events)
                task_state = ledger.project_state()
                if task_projection.contract is not None:
                    task_state["task"] = task_projection.to_context()
                progress_before = session.begin_turn(ledger)
                prepared = session.prepare_context(
                    ledger.derive_messages(),
                    environment_state=environment.state(),
                    task_state=task_state,
                )
                if isinstance(prepared, PreparedContext):
                    messages = list(prepared.messages)
                    ledger.append(
                        CONTEXT_SELECTED,
                        dict(prepared.audit),
                        turn=1,
                        step=step,
                    )
                else:
                    messages = list(prepared)
                messages = await self.kernel.events.dispatch(AGENT_PRE_STEP, list(messages))
                request = ModelRequest(messages, tool_runtime.schemas(), {"run_id": run_id, "turn": 1, "step": step})
                request = await self.kernel.events.dispatch(AGENT_REQUEST, request)
                ledger.append(
                    "request/header",
                    {
                        "messages": [dict(message) for message in request.messages],
                        "tools": [dict(tool) for tool in request.tools],
                        "context": dict(request.context),
                    },
                    turn=1,
                    step=step,
                )
                response = await model.complete(request)
                ledger.append(
                    "assistant/message",
                    {
                        "content": response.content,
                        "usage": dict(response.usage),
                        "tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": dict(call.arguments),
                            }
                            for call in response.tool_calls
                        ],
                    },
                    turn=1,
                    step=step,
                )
                if response.tool_calls:
                    for call in response.tool_calls:
                        ledger.append(
                            "tool/call",
                            {"call_id": call.id, "name": call.name, "arguments": dict(call.arguments)},
                            turn=1,
                            step=step,
                        )
                        task_writer = TaskEventWriter(ledger)
                        semantics = classify_tool_action(call.name, call.arguments)
                        delivery_required = bool(
                            TaskStateProjector()
                            .project(ledger.events)
                            .values.get("recovery.partial_delivery_requested")
                        ) and not any(
                            record.semantics.mutating and record.error_type is None
                            for record in action_ledger.records
                        )
                        budget_blocked = not turn_budget.admit(
                            "agent-turn-1",
                            mutating=semantics.mutating,
                        )
                        scope_blocked = session.is_action_blocked(call)
                        delivery_blocked = delivery_required and not semantics.mutating
                        if budget_blocked or scope_blocked or delivery_blocked:
                            if budget_blocked:
                                ledger.append(
                                    "resource/turn-budget-blocked",
                                    {
                                        "call_id": call.id,
                                        "maximum_nonmutating_actions": 20,
                                    },
                                    turn=1,
                                    step=step,
                                )
                            if scope_blocked:
                                session.record_blocked_action(
                                    ledger,
                                    call,
                                    turn=1,
                                    step=step,
                                )
                            if delivery_blocked:
                                ledger.append(
                                    "resource/delivery-first-blocked",
                                    {"call_id": call.id},
                                    turn=1,
                                    step=step,
                                )
                            result = ToolResult(
                                call.id,
                                (
                                    "Harness ended this turn after the non-mutating action budget "
                                    "was exhausted."
                                    if budget_blocked
                                    else (
                                        "[HARNESS DELIVERY REQUIRED] Read-only action blocked. "
                                        "Write a required artifact before further inspection."
                                        if delivery_blocked
                                        else "Harness blocked this repeated no-progress Action Scope."
                                    )
                                ),
                                error_type=(
                                    "HARNESS_TURN_BUDGET"
                                    if budget_blocked
                                    else "HARNESS_DELIVERY_REQUIRED"
                                    if delivery_blocked
                                    else "HARNESS_SCOPE_BLOCKED"
                                ),
                                metadata={"executed": False},
                            )
                            attempts = ()
                        else:
                            trace = await tool_runtime.execute_with_trace(call)
                            result = trace.result
                            attempts = trace.attempts
                        for attempt in attempts:
                            if attempt.failure is not None:
                                task_writer.classify_failure(
                                    Failure(
                                        id=f"{call.id}:attempt:{attempt.number}",
                                        error_type=attempt.failure.failure_type.value,
                                        message=attempt.failure.message,
                                        source=EvidenceSource.TOOL_RESULT,
                                        metadata={
                                            "attempt": attempt.number,
                                            "retryable": attempt.failure.retryable,
                                            "recovery": (
                                                attempt.recovery.action.value
                                                if attempt.recovery is not None
                                                else None
                                            ),
                                        },
                                    )
                                )
                        ledger.append(
                            "tool/result",
                            {
                                "call_id": result.call_id,
                                "content": result.content,
                                "error_type": result.error_type,
                                "metadata": {
                                    **dict(result.metadata),
                                    "attempts": len(attempts),
                                },
                            },
                            turn=1,
                            step=step,
                        )
                        record, decision = action_ledger.observe(call, result)
                        ledger.append(
                            "tool/action-audited",
                            {
                                "policy": "tool-action-ledger-v1",
                                "mode": action_ledger.config.mode.value,
                                "record": record.to_payload(),
                                "decision": decision.to_payload(),
                                "advice_applied": False,
                            },
                            turn=1,
                            step=step,
                        )
                        try:
                            for evidence in evidence_from_tool_result(result):
                                task_writer.add_evidence(evidence)
                        except (KeyError, TypeError, ValueError) as error:
                            task_writer.classify_failure(
                                Failure(
                                    id=f"{call.id}:evidence",
                                    error_type="INVALID_EVIDENCE",
                                    message=str(error),
                                    source=EvidenceSource.TOOL_RESULT,
                                )
                            )
                    progress = session.check_progress(ledger, progress_before)
                    session.check_resources(ledger, progress, turn=1)
                    ledger.append("step/end", {"reason": "tool_continuation"}, turn=1, step=step)
                    continue
                progress = session.check_progress(ledger, progress_before)
                session.check_resources(ledger, progress, turn=1)
                contract_result, feedback, _ = session.check_completion(
                    ledger,
                    response=response,
                )
                decision = CompletionDecision(contract_result.passed, feedback)
                intercepted = await self.kernel.events.dispatch(AGENT_TURN_STOPPING, decision)
                if intercepted != decision:
                    ledger.append(
                        "completion/intercepted",
                        {
                            "before": {
                                "passed": decision.passed,
                                "feedback": decision.feedback,
                            },
                            "after": {
                                "passed": intercepted.passed,
                                "feedback": intercepted.feedback,
                            },
                        },
                        turn=1,
                        step=step,
                    )
                decision = intercepted
                if decision.passed:
                    ledger.append("step/end", {"reason": "complete"}, turn=1, step=step)
                    ledger.append("turn/end", {"reason": "completed"}, turn=1)
                    ledger.append(
                        "runtime/end",
                        {"reason": "completed", "steps": step, "source": "agent-driver"},
                    )
                    return RunResult(run_id, response.content, True, step, ledger)
                ledger.append(
                    "user/message",
                    {"content": decision.feedback or "Completion rejected.", "source": "completion_policy"},
                    turn=1,
                    step=step,
                )
                ledger.append("step/end", {"reason": "completion_rejected"}, turn=1, step=step)
            ledger.append("turn/end", {"reason": "max_steps"}, turn=1)
            ledger.append(
                "runtime/end",
                {"reason": "max_steps", "steps": self.max_steps, "source": "agent-driver"},
            )
            return RunResult(run_id, "", False, self.max_steps, ledger)
        except Exception as error:
            if not any(event.type == "runtime/error" for event in ledger.events):
                ledger.append(
                    "runtime/error",
                    {"type": type(error).__name__, "message": str(error), "source": "agent-driver"},
                )
            raise
        finally:
            if cleanup_required:
                await environment.cleanup()
            if run_started:
                await self.kernel.events.dispatch(RUN_STOPPED, {"run_id": run_id})


class AgentDriverRuntimeAdapter:
    """Expose the synthetic AgentDriver through the shared Runtime contract."""

    def __init__(self, driver: AgentDriver) -> None:
        self.driver = driver

    async def run(self, request: RuntimeRequest) -> RuntimeResult:
        reject_runtime_credentials(request.client_options)
        reject_runtime_credentials(request.context)
        result = await self.driver.run(
            request.task,
            run_id=request.run_id,
            task_id=request.task_id,
            public_schema=request.public_schema,
            ledger_path=request.ledger_path,
        )
        return RuntimeResult(result.run_id, result.ledger, result.completed, result.content)


def _build_policy_session(
    kernel: Kernel,
    *,
    allow_unverified_completion: bool,
) -> KernelPolicySession:
    return KernelPolicySession(
        context_manager=(
            kernel.services.get(CONTEXT_MANAGER) if kernel.services.has(CONTEXT_MANAGER) else None
        ),
        response_completion_policy=kernel.services.get(COMPLETION_POLICY),
        contract_builder=(
            kernel.services.get(TASK_CONTRACT_BUILDER)
            if kernel.services.has(TASK_CONTRACT_BUILDER)
            else None
        ),
        completion_gate=(
            kernel.services.get(TASK_COMPLETION_GATE)
            if kernel.services.has(TASK_COMPLETION_GATE)
            else None
        ),
        progress_detector=(
            kernel.services.get(PROGRESS_DETECTOR)
            if kernel.services.has(PROGRESS_DETECTOR)
            else RuleBasedProgressDetector()
        ),
        recovery_policy=(
            kernel.services.get(TASK_RECOVERY_POLICY)
            if kernel.services.has(TASK_RECOVERY_POLICY)
            else None
        ),
        recovery_executor=(
            kernel.services.get(TASK_RECOVERY_EXECUTOR)
            if kernel.services.has(TASK_RECOVERY_EXECUTOR)
            else None
        ),
        recovery_outcome_evaluator=(
            kernel.services.get(RECOVERY_OUTCOME_EVALUATOR)
            if kernel.services.has(RECOVERY_OUTCOME_EVALUATOR)
            else None
        ),
        resource_guardrail=(
            kernel.services.get(RESOURCE_GUARDRAIL)
            if kernel.services.has(RESOURCE_GUARDRAIL)
            else ResourceGuardrail()
        ),
        unsupported_criteria="observe_only",
        allow_unverified_completion=allow_unverified_completion,
    )
