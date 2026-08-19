"""Translate DeerFlow stream events into the canonical ledger vocabulary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaptive_harness.ledger import SessionLedger


class DeerFlowEventAdapter:
    def append(self, ledger: SessionLedger, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type", "unknown"))
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if event_type == "messages-tuple" and data.get("type") == "ai":
            ledger.append("assistant/chunk", {"content": data.get("content", ""), "raw": dict(data)})
        elif event_type == "messages-tuple" and data.get("type") == "tool":
            ledger.append(
                "tool/result",
                {
                    "call_id": data.get("tool_call_id"),
                    "content": data.get("content", ""),
                    "metadata": {"source": "deerflow"},
                },
            )
        elif event_type == "end":
            ledger.append("runtime/end", {"usage": dict(data.get("usage") or {})})
        else:
            ledger.append(f"runtime/deerflow/{event_type}", {"data": dict(data)})
