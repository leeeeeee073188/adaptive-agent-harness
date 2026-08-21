"""Framework-neutral routing guard candidates evaluated before deployment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class GuardVerdict(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class GuardDecision:
    verdict: GuardVerdict
    browser_bypass: bool
    observed_bypass_count: int
    reason: str


class BrowserFallbackGuard:
    """Bound repeated shell/CDP browser bypass without restricting normal bash."""

    _BYPASS_PATTERNS = (
        re.compile(r"(?:127\.0\.0\.1|localhost):9222", re.IGNORECASE),
        re.compile(r"\bRuntime\.evaluate\b", re.IGNORECASE),
        re.compile(r"\b(?:websocket|webSocketDebuggerUrl)\b", re.IGNORECASE),
        re.compile(r"\bquerySelector(?:All)?\b", re.IGNORECASE),
        re.compile(r"\bchrome\s+devtools\b", re.IGNORECASE),
        re.compile(r"\bcdp\.", re.IGNORECASE),
    )

    def __init__(self, *, enabled: bool = False, max_browser_bypass_calls: int = 3) -> None:
        if max_browser_bypass_calls < 0:
            raise ValueError("max_browser_bypass_calls must be non-negative")
        self.enabled = enabled
        self.max_browser_bypass_calls = max_browser_bypass_calls
        self._observed = 0

    @classmethod
    def is_browser_bypass(cls, tool_name: str, arguments: Mapping[str, Any]) -> bool:
        if tool_name != "bash":
            return False
        command = str(arguments.get("command") or arguments.get("cmd") or "")
        return any(pattern.search(command) for pattern in cls._BYPASS_PATTERNS)

    def inspect(self, tool_name: str, arguments: Mapping[str, Any]) -> GuardDecision:
        bypass = self.is_browser_bypass(tool_name, arguments)
        if not bypass:
            return GuardDecision(GuardVerdict.ALLOW, False, self._observed, "Not a shell browser bypass.")
        self._observed += 1
        if self.enabled and self._observed > self.max_browser_bypass_calls:
            return GuardDecision(
                GuardVerdict.BLOCK,
                True,
                self._observed,
                "Browser shell/CDP fallback budget exhausted; use first-class browser tools.",
            )
        return GuardDecision(
            GuardVerdict.ALLOW,
            True,
            self._observed,
            "Browser shell/CDP fallback observed within the candidate budget.",
        )
