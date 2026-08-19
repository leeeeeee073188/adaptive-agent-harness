from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adaptive_harness.events import EventBus, EventMode, EventSpec
from adaptive_harness.ledger import SessionLedger


class EventLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_waterfall_wraps_and_short_circuits(self) -> None:
        bus = EventBus()
        spec = EventSpec("tool/execute", EventMode.WATERFALL)

        async def outer(payload, next_handler):
            result = await next_handler(payload + ["outer-before"])
            return result + ["outer-after"]

        async def terminal(payload, next_handler):
            return payload + ["terminal"]

        bus.subscribe(spec, outer, owner="outer")
        bus.subscribe(spec, terminal, owner="terminal")

        self.assertEqual(
            await bus.dispatch(spec, []),
            ["outer-before", "terminal", "outer-after"],
        )

    async def test_append_replay_and_message_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            ledger = SessionLedger("run-1", path)
            ledger.append("user/message", {"content": "hello"}, turn=1)
            ledger.append("assistant/message", {"content": "working"}, turn=1, step=1)
            ledger.append("tool/result", {"call_id": "c1", "content": "done"}, turn=1, step=1)
            ledger.append("state/updated", {"delta": {"artifact": "a.json"}})

            replayed = SessionLedger.replay(path)

        self.assertEqual([event.seq for event in replayed.events], [0, 1, 2, 3])
        self.assertEqual(replayed.derive_messages()[-1]["tool_call_id"], "c1")
        self.assertEqual(replayed.project_state(), {"artifact": "a.json"})
