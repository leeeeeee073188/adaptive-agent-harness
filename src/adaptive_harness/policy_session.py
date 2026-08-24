"""Core-owned policy session shared by fake and DeerFlow runtime adapters.

The session keeps runtime policy semantics together so adapters can differ in
transport and event translation without diverging on Completion, Progress,
Recovery, ResourceGuardrail, or context-selection behaviour.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from adaptive_harness.action_ledger import classify_tool_action
from adaptive_harness.capabilities import (
    CompletionPolicy,
    ContextManager,
    ModelResponse,
    PreparedContext,
    ToolCall,
)
from adaptive_harness.ledger import SessionLedger
from adaptive_harness.progress import ProgressDetector, ProgressResult, ProgressSnapshot, ProgressStatus
from adaptive_harness.recovery import (
    RecoveryOutcomeEvaluator,
    TaskFailureCategory,
    TaskFailureContext,
    TaskRecoveryAction,
    TaskRecoveryDecision,
    TaskRecoveryExecutor,
    TaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import (
    GuardrailObservation,
    NoProgressDisposition,
    ResourceGuardrail,
)
from adaptive_harness.task_contract import ContractBuilder, CriterionKind, RuleBasedTaskContractBuilder, TaskContract
from adaptive_harness.task_state import (
    ContractCompletionResult,
    EvidenceCompletionGate,
    EvidenceSource,
    Failure,
    RecoveryExecutionRecord,
    RecoveryRecord,
    TaskCompletionGate,
    TaskEventWriter,
    TaskStateProjector,
)

CriterionSupport = Callable[[Any], bool]
_CURRENT_POLICY_SESSION: ContextVar[Any] = ContextVar(
    "adaptive_policy_session",
    default=None,
)


@contextmanager
def bind_policy_session(session: KernelPolicySession) -> Iterator[None]:
    token = _CURRENT_POLICY_SESSION.set(session)
    try:
        yield
    finally:
        _CURRENT_POLICY_SESSION.reset(token)


def current_policy_session() -> KernelPolicySession | None:
    return _CURRENT_POLICY_SESSION.get()


class KernelPolicySession:
    """Hold the shared policy machinery for one runtime run."""

    def __init__(
        self,
        *,
        context_manager: ContextManager | None = None,
        response_completion_policy: CompletionPolicy | None = None,
        contract_builder: ContractBuilder | None = None,
        completion_gate: TaskCompletionGate | None = None,
        progress_detector: ProgressDetector | None = None,
        recovery_policy: TaskRecoveryPolicy | None = None,
        recovery_executor: TaskRecoveryExecutor | None = None,
        recovery_outcome_evaluator: RecoveryOutcomeEvaluator | None = None,
        resource_guardrail: ResourceGuardrail | None = None,
        unsupported_criteria: str = "reject",
        allow_unverified_completion: bool = False,
    ) -> None:
        if unsupported_criteria not in {"reject", "observe_only"}:
            raise ValueError("unsupported_criteria must be 'reject' or 'observe_only'")
        self.context_manager = context_manager
        self.response_completion_policy = response_completion_policy
        self.contract_builder = contract_builder or RuleBasedTaskContractBuilder()
        self.completion_gate = completion_gate
        self.progress_detector = progress_detector
        self.recovery_policy = recovery_policy
        self.recovery_executor = recovery_executor
        self.recovery_outcome_evaluator = recovery_outcome_evaluator
        self.resource_guardrail = resource_guardrail
        self.unsupported_criteria = unsupported_criteria
        self.allow_unverified_completion = allow_unverified_completion
        self._resource_cursor = -1
        self._task_prompt = ""
        self._turn_index = 0

    @property
    def turn_index(self) -> int:
        return self._turn_index

    def prepare_context(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        environment_state: Mapping[str, Any],
        task_state: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]] | PreparedContext:
        if self.context_manager is None:
            return tuple(dict(message) for message in messages)
        return self.context_manager.prepare(
            messages,
            environment_state=environment_state,
            task_state=task_state,
        )

    def is_action_blocked(self, call: ToolCall) -> bool:
        if self.resource_guardrail is None:
            return False
        semantics = classify_tool_action(call.name, call.arguments)
        return self.resource_guardrail.is_blocked(
            semantics.scope_key,
            semantics.argument_fingerprint,
        )

    def record_blocked_action(
        self,
        ledger: SessionLedger,
        call: ToolCall,
        *,
        turn: int,
        step: int | None = None,
    ) -> None:
        semantics = classify_tool_action(call.name, call.arguments)
        ledger.append(
            "resource/action-blocked",
            {
                "call_id": call.id,
                "scope_key": semantics.scope_key,
                "strategy_fingerprint": semantics.argument_fingerprint,
                "reason": "Action Scope is blocked after three no-progress attempts.",
            },
            turn=turn,
            step=step,
        )

    def start_contract(
        self,
        ledger: SessionLedger,
        *,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, object] | None,
        criterion_supported: CriterionSupport | None = None,
    ) -> TaskContract:
        contract = self.contract_builder.build(task_id, task_prompt, public_schema)
        self._task_prompt = task_prompt
        if self.unsupported_criteria == "observe_only" and criterion_supported is not None:
            criteria = tuple(
                criterion
                if not criterion.required or criterion_supported(criterion)
                else replace(criterion, required=False)
                for criterion in contract.criteria
            )
            contract = TaskContract(
                contract.task_id,
                contract.original_request,
                criteria,
                contract.public_schema_hash,
            )
        TaskEventWriter(ledger).create_contract(contract)
        ledger.append(
            "policy/configured",
            {
                "completion_gate": "evidence" if self.completion_gate is not None else "disabled",
                "unsupported_criteria": self.unsupported_criteria,
                "allow_unverified_completion": self.allow_unverified_completion,
                "enforced_criterion_ids": [
                    criterion.id for criterion in contract.criteria if criterion.required
                ],
                "observe_only_criterion_ids": [
                    criterion.id for criterion in contract.criteria if not criterion.required
                ],
            },
        )
        return contract

    def begin_turn(self, ledger: SessionLedger) -> ProgressSnapshot | None:
        self._turn_index += 1
        if self.progress_detector is None:
            return None
        state = TaskStateProjector().project(ledger.events)
        return self.progress_detector.snapshot(state)

    def check_progress(
        self,
        ledger: SessionLedger,
        before: ProgressSnapshot | None,
    ) -> ProgressResult | None:
        if self.progress_detector is None or before is None:
            return None
        state = TaskStateProjector().project(ledger.events)
        result = self.progress_detector.detect(before, self.progress_detector.snapshot(state))
        ledger.append("progress/checked", result.to_payload())
        TaskEventWriter(ledger).update_state(
            {
                "progress.last_status": result.status.value,
                "progress.last_fingerprint": result.after_fingerprint,
            },
            reason="semantic progress checked",
        )
        return result

    def check_resources(
        self,
        ledger: SessionLedger,
        progress: ProgressResult | None,
        *,
        turn: int,
    ) -> tuple[dict[str, object], ...]:
        if self.resource_guardrail is None:
            return ()
        audited = [
            event
            for event in ledger.events
            if event.type == "tool/action-audited"
            and event.turn == turn
            and event.seq > self._resource_cursor
        ]
        decisions: list[dict[str, object]] = []
        for index, event in enumerate(audited):
            record = event.payload.get("record") or {}
            if not isinstance(record, Mapping):
                continue
            intent = str(record.get("intent") or "")
            observation = GuardrailObservation(
                action_scope=str(record.get("scope_key") or ""),
                strategy_fingerprint=str(record.get("argument_fingerprint") or ""),
                mutation_epoch=int(record.get("mutation_epoch") or 0),
                semantic_progress=bool(
                    progress is not None
                    and progress.status is ProgressStatus.PROGRESSED
                    and index == len(audited) - 1
                ),
                post_mutation_verification=(
                    intent in {"read", "observe", "verify"}
                    and int(record.get("mutation_epoch") or 0) > 0
                ),
            )
            decision = self.resource_guardrail.observe(observation)
            payload: dict[str, object] = {
                "action_event_seq": event.seq,
                "scope_key": observation.action_scope,
                "strategy_fingerprint": observation.strategy_fingerprint,
                "mutation_epoch": observation.mutation_epoch,
                **decision.to_payload(),
            }
            ledger.append("resource/no-progress-checked", payload, turn=turn)
            decisions.append(payload)
            if decision.disposition in {
                NoProgressDisposition.REPLAN,
                NoProgressDisposition.BLOCK_SCOPE,
            }:
                TaskEventWriter(ledger).classify_failure(
                    Failure(
                        id=f"resource:t{turn}:a{event.seq}",
                        error_type=(
                            "LOOP"
                            if decision.disposition is NoProgressDisposition.BLOCK_SCOPE
                            else "NO_PROGRESS"
                        ),
                        message=decision.reason,
                        source=EvidenceSource.RUNTIME_OBSERVATION,
                        metadata={"scope_key": observation.action_scope},
                    )
                )
        if audited:
            self._resource_cursor = max(event.seq for event in audited)
        if decisions:
            maximum = max(int(item["consecutive_no_progress"]) for item in decisions)
            blocked = any(item["disposition"] == "block_scope" for item in decisions)
            TaskEventWriter(ledger).update_state(
                {
                    "resource.no_progress_streak": maximum,
                    "resource.blocked_scope": blocked,
                },
                reason="resource guardrail evaluated",
            )
        return tuple(decisions)

    def check_completion(
        self,
        ledger: SessionLedger,
        *,
        response: ModelResponse | None = None,
    ) -> tuple[ContractCompletionResult, str | None, TaskRecoveryDecision | None]:
        state = TaskStateProjector().project(ledger.events)
        response_decision = (
            self.response_completion_policy.check(
                self._task_prompt,
                response,
                ledger.project_state(),
            )
            if self.response_completion_policy is not None and response is not None
            else None
        )
        if response_decision is not None and not response_decision.passed:
            result = ContractCompletionResult(
                False,
                (),
                ("acceptable final response",),
                "Response completion policy rejected the final response.",
            )
        elif self.completion_gate is None and self.allow_unverified_completion:
            result = ContractCompletionResult(
                True,
                (),
                (),
                "Explicit legacy mode allows completion without an Evidence Gate.",
            )
        elif self.completion_gate is None:
            result = ContractCompletionResult(
                False,
                (),
                ("completion gate",),
                "Completion Gate is required unless explicit legacy mode is enabled.",
            )
        elif (
            self.unsupported_criteria == "observe_only"
            and state.contract is not None
            and not any(criterion.required for criterion in state.contract.criteria)
        ):
            result = ContractCompletionResult(
                self.allow_unverified_completion,
                (),
                (() if self.allow_unverified_completion else ("provider-backed criteria",)),
                (
                    "Explicit legacy mode allows observe-only completion."
                    if self.allow_unverified_completion
                    else "Provider-backed criteria are required for completion."
                ),
            )
        else:
            result = self.completion_gate.verify(state)
        feedback = response_decision.feedback if response_decision is not None else None
        if (
            not result.passed
            and (response_decision is None or response_decision.passed)
            and self.completion_gate is not None
        ):
            feedback = self.completion_gate.feedback(result)
        elif not result.passed and feedback is None:
            feedback = result.reason
        ledger.append("completion/checked", {**result.to_payload(), "feedback": feedback})
        self._evaluate_pending_recovery(ledger, result)
        recovery = None
        if not result.passed and self.recovery_policy is not None:
            context = self._failure_context(state, result)
            recovery = self.recovery_policy.decide(context)
            TaskEventWriter(ledger).record_recovery(
                RecoveryRecord(context.primary, context.secondary, recovery)
            )
            actions = ", ".join(action.value for action in recovery.actions)
            feedback = (
                f"{feedback}\nRecovery actions: {actions}."
                if feedback
                else f"Recovery actions: {actions}."
            )
            if self.recovery_executor is not None:
                execution = self.recovery_executor.execute(
                    recovery,
                    missing=result.missing,
                )
                TaskEventWriter(ledger).record_recovery_execution(
                    RecoveryExecutionRecord(execution)
                )
                if execution.directives:
                    feedback = f"{feedback}\n" + "\n".join(execution.directives)
        return result, feedback, recovery

    def _evaluate_pending_recovery(
        self,
        ledger: SessionLedger,
        completion: ContractCompletionResult,
    ) -> None:
        if self.recovery_outcome_evaluator is None:
            return
        evaluated = {
            int(event.payload["execution_seq"])
            for event in ledger.events
            if event.type == "recovery/outcome-evaluated"
        }
        pending = [
            event
            for event in ledger.events
            if event.type == "recovery/executed" and event.seq not in evaluated
        ]
        if not pending:
            return
        execution_event = pending[-1]
        progress_events = [
            event
            for event in ledger.events
            if event.type == "progress/checked" and event.seq > execution_event.seq
        ]
        if not progress_events:
            return
        execution = RecoveryExecutionRecord.from_payload(execution_event.payload).execution
        outcome = self.recovery_outcome_evaluator.evaluate(
            execution_seq=execution_event.seq,
            execution=execution,
            progress_status=str(progress_events[-1].payload["status"]),
            completion_passed=completion.passed,
        )
        TaskEventWriter(ledger).record_recovery_outcome(outcome)

    def _failure_context(
        self,
        state: Any,
        result: ContractCompletionResult,
    ) -> TaskFailureContext:
        by_id = {
            criterion.id: criterion
            for criterion in (state.contract.criteria if state.contract is not None else ())
        }
        failed_kinds = {
            by_id[assessment.criterion_id].kind
            for assessment in result.assessments
            if assessment.status.value != "satisfied" and assessment.criterion_id in by_id
        }
        primary = (
            TaskFailureCategory.ARTIFACT_ERROR
            if CriterionKind.ARTIFACT_EXISTS in failed_kinds
            else TaskFailureCategory.CONSTRAINT_MISS
            if CriterionKind.EXACT_COUNT in failed_kinds
            else TaskFailureCategory.STATE_INCONSISTENCY
            if CriterionKind.OBSERVATION_EQUALS in failed_kinds
            else TaskFailureCategory.PREMATURE_FINISH
        )
        attempts: dict[TaskRecoveryAction, int] = {}
        for record in state.recoveries:
            for action in record.decision.actions:
                attempts[action] = attempts.get(action, 0) + 1
        secondary = [TaskFailureCategory.PREMATURE_FINISH]
        progress_status = state.values.get("progress.last_status")
        if progress_status == ProgressStatus.NO_PROGRESS.value:
            secondary.append(TaskFailureCategory.NO_PROGRESS)
        elif progress_status == ProgressStatus.REGRESSED.value:
            secondary.append(TaskFailureCategory.STATE_INCONSISTENCY)
        repeated_action_count = int(state.values.get("resource.no_progress_streak") or 0)
        if state.values.get("resource.blocked_scope"):
            secondary.append(TaskFailureCategory.LOOP)
        return TaskFailureContext(
            primary,
            tuple(secondary),
            repeated_action_count=repeated_action_count,
            attempts=attempts,
        )


class PolicySession(KernelPolicySession):
    """Backward-compatible policy session API for runtime bridges.

    ``KernelPolicySession`` is the shared core implementation. This facade keeps
    the existing bridge-facing method names while adding the turn budget and
    criterion-support callback expected by embedded runtime adapters.
    """

    def __init__(
        self,
        *,
        context_manager: ContextManager | None = None,
        response_completion_policy: CompletionPolicy | None = None,
        contract_builder: ContractBuilder | None = None,
        completion_gate: TaskCompletionGate | None = None,
        max_completion_turns: int = 2,
        unsupported_criteria: str = "reject",
        criterion_supported: CriterionSupport | None = None,
        progress_detector: ProgressDetector | None = None,
        recovery_policy: TaskRecoveryPolicy | None = None,
        recovery_executor: TaskRecoveryExecutor | None = None,
        recovery_outcome_evaluator: RecoveryOutcomeEvaluator | None = None,
        resource_guardrail: ResourceGuardrail | None = None,
        allow_unverified_completion: bool = False,
    ) -> None:
        if max_completion_turns < 1:
            raise ValueError("max_completion_turns must be at least one")
        super().__init__(
            context_manager=context_manager,
            response_completion_policy=response_completion_policy,
            contract_builder=contract_builder,
            completion_gate=completion_gate or EvidenceCompletionGate(),
            progress_detector=progress_detector,
            recovery_policy=recovery_policy,
            recovery_executor=recovery_executor,
            recovery_outcome_evaluator=recovery_outcome_evaluator,
            resource_guardrail=resource_guardrail,
            unsupported_criteria=unsupported_criteria,
            allow_unverified_completion=allow_unverified_completion,
        )
        self.max_completion_turns = max_completion_turns
        self.criterion_supported = criterion_supported

    def start(
        self,
        ledger: SessionLedger,
        *,
        task_id: str,
        task_prompt: str,
        public_schema: Mapping[str, object] | None,
    ) -> TaskContract:
        return self.start_contract(
            ledger,
            task_id=task_id,
            task_prompt=task_prompt,
            public_schema=public_schema,
            criterion_supported=self.criterion_supported,
        )
