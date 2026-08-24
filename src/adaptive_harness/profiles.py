"""Default executable policy profile for the adaptive harness core.

This module keeps policy composition benchmark-agnostic.  It turns a stable
``Profile`` into mounted Kernel services, then lets runtime adapters create a
fresh ``PolicySession`` from those services without importing any adapter- or
benchmark-specific code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from adaptive_harness.assembly import PluginRegistry, assemble_profile
from adaptive_harness.capabilities import AcceptFinalCompletion
from adaptive_harness.config import Bundle, PluginSpec, Profile
from adaptive_harness.context import (
    ContextBudget,
    ExperienceRetriever,
    TaskAwareContextManager,
)
from adaptive_harness.kernel import Kernel, PluginContext
from adaptive_harness.policy_session import PolicySession
from adaptive_harness.progress import RuleBasedProgressDetector
from adaptive_harness.recovery import (
    RuleBasedRecoveryOutcomeEvaluator,
    RuleBasedTaskRecoveryExecutor,
    RuleBasedTaskRecoveryPolicy,
)
from adaptive_harness.resource_guardrail import ResourceGuardrail
from adaptive_harness.services import (
    COMPLETION_POLICY,
    CONTEXT_MANAGER,
    PROGRESS_DETECTOR,
    RECOVERY_OUTCOME_EVALUATOR,
    RESOURCE_GUARDRAIL,
    TASK_COMPLETION_GATE,
    TASK_CONTRACT_BUILDER,
    TASK_RECOVERY_EXECUTOR,
    TASK_RECOVERY_POLICY,
)
from adaptive_harness.task_contract import ContractBuilder, RuleBasedTaskContractBuilder
from adaptive_harness.task_state import EvidenceCompletionGate

_POLICY_PLUGIN_ORDER = (
    "context",
    "response_completion",
    "task_contract_builder",
    "evidence_completion",
    "semantic_progress",
    "durable_recovery",
    "resource_guardrail",
)
_ALLOWED_CONTEXT_CONFIG = {"max_input_tokens", "recent_history_fraction"}
_ALLOWED_EMPTY_CONFIGS = {
    "response_completion",
    "task_contract_builder",
    "evidence_completion",
    "semantic_progress",
    "durable_recovery",
    "resource_guardrail",
}


@dataclass(frozen=True)
class _ContextPlugin:
    experience_retriever: ExperienceRetriever | None = None

    name = "context"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        config = _validate_config(self.name, context.config, _ALLOWED_CONTEXT_CONFIG)
        budget = ContextBudget(
            max_input_tokens=int(config.get("max_input_tokens", ContextBudget.max_input_tokens)),
            recent_history_fraction=float(
                config.get("recent_history_fraction", ContextBudget.recent_history_fraction)
            ),
        )
        context.provide(
            CONTEXT_MANAGER,
            TaskAwareContextManager(
                budget=budget,
                experience_retriever=self.experience_retriever,
            ),
        )


@dataclass(frozen=True)
class _TaskContractBuilderPlugin:
    contract_builder: ContractBuilder | None = None

    name = "task_contract_builder"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(
            TASK_CONTRACT_BUILDER,
            self.contract_builder or RuleBasedTaskContractBuilder(),
        )


class _ResponseCompletionPlugin:
    name = "response_completion"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(COMPLETION_POLICY, AcceptFinalCompletion())


class _EvidenceCompletionPlugin:
    name = "evidence_completion"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(TASK_COMPLETION_GATE, EvidenceCompletionGate())


class _SemanticProgressPlugin:
    name = "semantic_progress"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(PROGRESS_DETECTOR, RuleBasedProgressDetector())


class _DurableRecoveryPlugin:
    name = "durable_recovery"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(TASK_RECOVERY_POLICY, RuleBasedTaskRecoveryPolicy())
        context.provide(TASK_RECOVERY_EXECUTOR, RuleBasedTaskRecoveryExecutor())
        context.provide(RECOVERY_OUTCOME_EVALUATOR, RuleBasedRecoveryOutcomeEvaluator())


class _ResourceGuardrailPlugin:
    name = "resource_guardrail"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        _validate_empty_config(self.name, context.config)
        context.provide(RESOURCE_GUARDRAIL, ResourceGuardrail())


def candidate_policy_profile(
    *,
    overlays: Sequence[PluginSpec] = (),
    context_config: Mapping[str, Any] | None = None,
) -> Profile:
    """Return the default benchmark-agnostic candidate policy Profile.

    The Profile is data-only and fingerprintable. Runtime-only objects such as
    contract builders and experience retrievers are injected into
    ``assemble_policy_kernel`` so credentials or mutable stores never enter the
    Profile fingerprint.
    """

    base_specs = tuple(
        PluginSpec(
            name,
            dict(context_config or {}) if name == "context" and context_config else {},
        )
        for name in _POLICY_PLUGIN_ORDER
    )
    return Profile(
        "candidate_policy_profile",
        (Bundle("policy-core", base_specs),),
        tuple(overlays),
    )


DEFAULT_POLICY_PROFILE = candidate_policy_profile()


async def assemble_policy_kernel(
    profile: Profile | None = None,
    *,
    contract_builder: ContractBuilder | None = None,
    experience_retriever: ExperienceRetriever | None = None,
    kernel: Kernel | None = None,
) -> Kernel:
    """Mount a policy Profile into a Kernel using a closed default registry."""

    registry = _policy_plugin_registry(
        contract_builder=contract_builder,
        experience_retriever=experience_retriever,
    )
    return await assemble_profile(profile or DEFAULT_POLICY_PROFILE, registry, kernel=kernel)


def policy_session_from_kernel(kernel: Kernel) -> PolicySession:
    """Create a new PolicySession from already assembled Kernel services."""

    return PolicySession(
        context_manager=kernel.services.get(CONTEXT_MANAGER),
        response_completion_policy=kernel.services.get(COMPLETION_POLICY),
        contract_builder=kernel.services.get(TASK_CONTRACT_BUILDER),
        completion_gate=kernel.services.get(TASK_COMPLETION_GATE),
        progress_detector=kernel.services.get(PROGRESS_DETECTOR),
        recovery_policy=kernel.services.get(TASK_RECOVERY_POLICY),
        recovery_executor=kernel.services.get(TASK_RECOVERY_EXECUTOR),
        recovery_outcome_evaluator=kernel.services.get(RECOVERY_OUTCOME_EVALUATOR),
        resource_guardrail=kernel.services.get(RESOURCE_GUARDRAIL),
        unsupported_criteria="observe_only",
    )


def _policy_plugin_registry(
    *,
    contract_builder: ContractBuilder | None,
    experience_retriever: ExperienceRetriever | None,
) -> PluginRegistry:
    return PluginRegistry.from_entries(
        (
            ("context", lambda spec: _ContextPlugin(experience_retriever)),
            ("response_completion", lambda spec: _ResponseCompletionPlugin()),
            (
                "task_contract_builder",
                lambda spec: _TaskContractBuilderPlugin(contract_builder),
            ),
            ("evidence_completion", lambda spec: _EvidenceCompletionPlugin()),
            ("semantic_progress", lambda spec: _SemanticProgressPlugin()),
            ("durable_recovery", lambda spec: _DurableRecoveryPlugin()),
            ("resource_guardrail", lambda spec: _ResourceGuardrailPlugin()),
        )
    )


def _validate_empty_config(name: str, config: Mapping[str, Any]) -> None:
    _validate_config(name, config, ())


def _validate_config(
    name: str,
    config: Mapping[str, Any],
    allowed: Sequence[str] | set[str],
) -> Mapping[str, Any]:
    unknown = sorted(set(config) - set(allowed))
    if unknown:
        raise ValueError(f"unknown config for policy plugin {name!r}: {', '.join(unknown)}")
    if name in _ALLOWED_EMPTY_CONFIGS and config:
        raise ValueError(f"unknown config for policy plugin {name!r}: {', '.join(sorted(config))}")
    return config
