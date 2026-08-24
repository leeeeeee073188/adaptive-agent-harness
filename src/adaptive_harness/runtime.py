"""Turn/step driver whose inputs and outputs are fully reconstructable."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.capabilities import CompletionDecision, ModelRequest, PreparedContext
from adaptive_harness.context import CONTEXT_SELECTED
from adaptive_harness.kernel import Kernel
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.lifecycle import AGENT_PRE_STEP, AGENT_REQUEST, AGENT_TURN_STOPPING, RUN_STARTED, RUN_STOPPED
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    ENVIRONMENT,
    MODEL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
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

    def __init__(self, kernel: Kernel, *, max_steps: int = 64) -> None:
        self.kernel = kernel
        self.max_steps = max_steps

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
        environment = self.kernel.services.get(ENVIRONMENT)
        model = self.kernel.services.get(MODEL)
        context_manager = self.kernel.services.get(CONTEXT_MANAGER)
        tool_runtime = self.kernel.services.get(TOOL_RUNTIME)
        completion = self.kernel.services.get(COMPLETION_POLICY)
        cleanup_required = False
        run_started = False
        try:
            cleanup_required = True
            await environment.build()
            tool_runtime = tool_runtime.fork(await environment.tools())
            await self.kernel.events.dispatch(RUN_STARTED, {"run_id": run_id, "task": task})
            run_started = True
            if self.kernel.services.has(TASK_CONTRACT_BUILDER):
                contract_builder = self.kernel.services.get(TASK_CONTRACT_BUILDER)
                contract = contract_builder.build(task_id or run_id, task, public_schema)
                TaskEventWriter(ledger).create_contract(contract)
            ledger.append("turn/start", {"trigger": "task"}, turn=1)
            ledger.append("user/message", {"content": task, "source": "task"}, turn=1)
            for step in range(1, self.max_steps + 1):
                ledger.append("step/start", {}, turn=1, step=step)
                task_state = ledger.project_state()
                task_projection = TaskStateProjector().project(ledger.events)
                if task_projection.contract is not None:
                    task_state["task"] = task_projection.to_context()
                prepared = context_manager.prepare(
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
                        trace = await tool_runtime.execute_with_trace(call)
                        result = trace.result
                        task_writer = TaskEventWriter(ledger)
                        for attempt in trace.attempts:
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
                                    "attempts": len(trace.attempts),
                                },
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
                    ledger.append("step/end", {"reason": "tool_continuation"}, turn=1, step=step)
                    continue
                decision = completion.check(task, response, ledger.project_state())
                decision = await self.kernel.events.dispatch(AGENT_TURN_STOPPING, decision)
                completion_payload: dict[str, object] = {
                    "passed": decision.passed,
                    "feedback": decision.feedback,
                }
                task_projection = TaskStateProjector().project(ledger.events)
                if (
                    decision.passed
                    and task_projection.contract is not None
                    and self.kernel.services.has(TASK_COMPLETION_GATE)
                ):
                    gate = self.kernel.services.get(TASK_COMPLETION_GATE)
                    contract_result = gate.verify(task_projection)
                    feedback = None if contract_result.passed else gate.feedback(contract_result)
                    decision = CompletionDecision(contract_result.passed, feedback)
                    completion_payload = {
                        **contract_result.to_payload(),
                        "feedback": feedback,
                    }
                ledger.append(
                    "completion/checked",
                    completion_payload,
                    turn=1,
                    step=step,
                )
                if decision.passed:
                    ledger.append("step/end", {"reason": "complete"}, turn=1, step=step)
                    ledger.append("turn/end", {"reason": "completed"}, turn=1)
                    return RunResult(run_id, response.content, True, step, ledger)
                ledger.append(
                    "user/message",
                    {"content": decision.feedback or "Completion rejected.", "source": "completion_policy"},
                    turn=1,
                    step=step,
                )
                ledger.append("step/end", {"reason": "completion_rejected"}, turn=1, step=step)
            ledger.append("turn/end", {"reason": "max_steps"}, turn=1)
            return RunResult(run_id, "", False, self.max_steps, ledger)
        finally:
            if cleanup_required:
                await environment.cleanup()
            if run_started:
                await self.kernel.events.dispatch(RUN_STOPPED, {"run_id": run_id})
