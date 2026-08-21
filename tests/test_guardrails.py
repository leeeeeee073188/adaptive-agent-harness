from __future__ import annotations

import json
import unittest
from pathlib import Path

from adaptive_harness.guardrails import BrowserFallbackGuard, GuardVerdict


class BrowserFallbackGuardTests(unittest.TestCase):
    def test_safe_file_and_api_bash_are_not_browser_bypass(self) -> None:
        guard = BrowserFallbackGuard(enabled=True, max_browser_bypass_calls=0)

        file_decision = guard.inspect("bash", {"command": "python3 build_report.py"})
        api_decision = guard.inspect("bash", {"command": "curl http://127.0.0.1:3071/mcp"})

        self.assertEqual(file_decision.verdict, GuardVerdict.ALLOW)
        self.assertEqual(api_decision.verdict, GuardVerdict.ALLOW)
        self.assertFalse(file_decision.browser_bypass)
        self.assertFalse(api_decision.browser_bypass)

    def test_explicit_cdp_and_dom_shell_patterns_are_detected(self) -> None:
        self.assertTrue(
            BrowserFallbackGuard.is_browser_bypass(
                "bash",
                {"command": "curl http://127.0.0.1:9222/json"},
            )
        )
        self.assertTrue(
            BrowserFallbackGuard.is_browser_bypass(
                "bash",
                {"command": "Runtime.evaluate({expression: 'document.querySelector(\"form\")'})"},
            )
        )

    def test_fourth_bypass_is_blocked_after_three_call_budget(self) -> None:
        guard = BrowserFallbackGuard(enabled=True, max_browser_bypass_calls=3)
        decisions = [
            guard.inspect("bash", {"command": "curl http://localhost:9222/json"})
            for _ in range(4)
        ]

        self.assertEqual([item.verdict for item in decisions[:3]], [GuardVerdict.ALLOW] * 3)
        self.assertEqual(decisions[3].verdict, GuardVerdict.BLOCK)

    def test_disabled_guard_observes_without_blocking(self) -> None:
        guard = BrowserFallbackGuard(enabled=False, max_browser_bypass_calls=0)

        decision = guard.inspect("bash", {"command": "curl http://localhost:9222/json"})

        self.assertEqual(decision.verdict, GuardVerdict.ALLOW)
        self.assertEqual(decision.observed_bypass_count, 1)

    def test_deterministic_success_controls_have_zero_candidate_blocks(self) -> None:
        path = Path(__file__).parent / "fixtures/browser_guard_success_controls.json"
        controls = json.loads(path.read_text())["controls"]

        for control in controls:
            guard = BrowserFallbackGuard(enabled=True, max_browser_bypass_calls=3)
            decisions = [
                guard.inspect(call["name"], call.get("args") or {})
                for call in control["tool_calls"]
            ]
            self.assertTrue(
                all(decision.verdict is GuardVerdict.ALLOW for decision in decisions),
                control["id"],
            )


if __name__ == "__main__":
    unittest.main()
