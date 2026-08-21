from __future__ import annotations

import unittest

from adaptive_harness.capabilities import ToolCall, ToolDefinition, ToolResult
from adaptive_harness.tool_reliability import RecoveryAction, ToolFailureType, ToolReliabilityConfig
from adaptive_harness.tool_runtime import ToolRuntime


class ToolReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failure_retries_within_budget(self) -> None:
        calls = 0

        def flaky() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError("service timed out")
            return "ok"

        runtime = ToolRuntime(
            [ToolDefinition("flaky", "flaky operation", flaky)],
            reliability=ToolReliabilityConfig(enabled=True, max_attempts=3),
        )

        trace = await runtime.execute_with_trace(ToolCall("c1", "flaky", {}))

        self.assertEqual(len(trace.attempts), 2)
        self.assertEqual(trace.result.content, "ok")
        self.assertEqual(trace.attempts[0].failure.failure_type, ToolFailureType.TIMEOUT)
        self.assertEqual(trace.attempts[0].recovery.action, RecoveryAction.RETRY)

    async def test_permanent_failure_does_not_retry(self) -> None:
        def invalid() -> None:
            raise ValueError("bad argument")

        runtime = ToolRuntime(
            [ToolDefinition("invalid", "invalid operation", invalid)],
            reliability=ToolReliabilityConfig(enabled=True, max_attempts=3),
        )

        trace = await runtime.execute_with_trace(ToolCall("c2", "invalid", {}))

        self.assertEqual(len(trace.attempts), 1)
        self.assertEqual(trace.attempts[0].failure.failure_type, ToolFailureType.INVALID_ARGUMENT)
        self.assertEqual(trace.attempts[0].recovery.action, RecoveryAction.STOP)

    async def test_disabled_reliability_preserves_single_attempt_baseline(self) -> None:
        calls = 0

        def timeout() -> None:
            nonlocal calls
            calls += 1
            raise TimeoutError("timed out")

        runtime = ToolRuntime([ToolDefinition("timeout", "timeout operation", timeout)])

        trace = await runtime.execute_with_trace(ToolCall("c3", "timeout", {}))

        self.assertEqual(calls, 1)
        self.assertEqual(len(trace.attempts), 1)
        self.assertIsNone(trace.attempts[0].failure)

    async def test_structured_tool_result_keeps_evidence_metadata(self) -> None:
        def inspect() -> ToolResult:
            return ToolResult(
                "provider-id",
                "found",
                metadata={
                    "evidence": [
                        {"kind": "artifact", "subject": "outputs/report.csv", "value": True}
                    ]
                },
            )

        result = await ToolRuntime([ToolDefinition("inspect", "inspect artifact", inspect)]).execute(
            ToolCall("canonical-id", "inspect", {})
        )

        self.assertEqual(result.call_id, "canonical-id")
        self.assertEqual(result.metadata["evidence"][0]["kind"], "artifact")


if __name__ == "__main__":
    unittest.main()
