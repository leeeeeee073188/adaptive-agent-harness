"""Canonical service keys used by the default runtime composition."""

from adaptive_harness.capabilities import CompletionPolicy, ContextManager, Environment, ModelAdapter
from adaptive_harness.kernel import ServiceKey
from adaptive_harness.task_contract import ContractBuilder
from adaptive_harness.tool_runtime import ToolRuntime

MODEL = ServiceKey[ModelAdapter]("model")
ENVIRONMENT = ServiceKey[Environment]("environment")
CONTEXT_MANAGER = ServiceKey[ContextManager]("context_manager")
TOOL_RUNTIME = ServiceKey[ToolRuntime]("tool_runtime")
COMPLETION_POLICY = ServiceKey[CompletionPolicy]("completion_policy")
TASK_CONTRACT_BUILDER = ServiceKey[ContractBuilder]("task_contract_builder")
