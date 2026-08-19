"""Turn/step driver whose inputs and outputs are fully reconstructable."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.capabilities import ModelRequest
from adaptive_harness.kernel import Kernel
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.lifecycle import AGENT_PRE_STEP, AGENT_REQUEST, AGENT_TURN_STOPPING, RUN_STARTED, RUN_STOPPED
from adaptive_harness.services import COMPLETION_POLICY, CONTEXT_MANAGER, ENVIRONMENT, MODEL, TOOL_RUNTIME


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

    async def run(self, task: str, *, run_id: str | None = None, ledger_path: Path | None = None) -> RunResult:
        run_id = run_id or str(uuid.uuid4())
        ledger = SessionLedger(run_id, ledger_path)
        environment = self.kernel.services.get(ENVIRONMENT)
        model = self.kernel.services.get(MODEL)
        context_manager = self.kernel.services.get(CONTEXT_MANAGER)
        tool_runtime = self.kernel.services.get(TOOL_RUNTIME)
        completion = self.kernel.services.get(COMPLETION_POLICY)
        await environment.build()
        tool_runtime = tool_runtime.fork(await environment.tools())
        await self.kernel.events.dispatch(RUN_STARTED, {"run_id": run_id, "task": task})
        ledger.append("turn/start", {"trigger": "task"}, turn=1)
        ledger.append("user/message", {"content": task, "source": "task"}, turn=1)
        try:
            for step in range(1, self.max_steps + 1):
                ledger.append("step/start", {}, turn=1, step=step)
                messages = context_manager.prepare(
                    ledger.derive_messages(),
                    environment_state=environment.state(),
                    task_state=ledger.project_state(),
                )
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
                    {"content": response.content, "usage": dict(response.usage)},
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
                        result = await tool_runtime.execute(call)
                        ledger.append(
                            "tool/result",
                            {
                                "call_id": result.call_id,
                                "content": result.content,
                                "error_type": result.error_type,
                                "metadata": dict(result.metadata),
                            },
                            turn=1,
                            step=step,
                        )
                    ledger.append("step/end", {"reason": "tool_continuation"}, turn=1, step=step)
                    continue
                decision = completion.check(task, response, ledger.project_state())
                decision = await self.kernel.events.dispatch(AGENT_TURN_STOPPING, decision)
                ledger.append(
                    "completion/checked",
                    {"passed": decision.passed, "feedback": decision.feedback},
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
            await environment.cleanup()
            await self.kernel.events.dispatch(RUN_STOPPED, {"run_id": run_id})
