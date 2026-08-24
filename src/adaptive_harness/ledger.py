"""Append-only session ledger and deterministic projections."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionEvent:
    seq: int
    time_ms: int
    run_id: str
    type: str
    payload: Mapping[str, Any]
    turn: int | None = None
    step: int | None = None


class SessionLedger:
    """Canonical facts; model messages and state are projections, never peers."""

    def __init__(self, run_id: str, path: Path | None = None, seed: Iterable[SessionEvent] = ()) -> None:
        self.run_id = run_id
        self.path = path
        self._events = list(seed)
        if any(event.seq != index for index, event in enumerate(self._events)):
            raise ValueError("seed ledger sequence must be contiguous from zero")
        if any(event.run_id != run_id for event in self._events):
            raise ValueError("seed events must belong to the same run")

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        turn: int | None = None,
        step: int | None = None,
    ) -> SessionEvent:
        json.dumps(payload)
        event = SessionEvent(
            seq=len(self._events),
            time_ms=int(time.time() * 1000),
            run_id=self.run_id,
            type=event_type,
            payload=dict(payload),
            turn=turn,
            step=step,
        )
        self._events.append(event)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n")
                file.flush()
        return event

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        return tuple(self._events)

    def derive_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for event in self._events:
            if event.type == "user/message":
                messages.append({"role": "user", "content": event.payload["content"]})
            elif event.type == "assistant/message":
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": event.payload.get("content", ""),
                }
                if event.payload.get("tool_calls"):
                    message["tool_calls"] = [
                        dict(item) for item in event.payload["tool_calls"]
                    ]
                messages.append(message)
            elif event.type == "tool/result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": event.payload["call_id"],
                        "content": event.payload.get("content", ""),
                    }
                )
        return messages

    def project_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for event in self._events:
            if event.type == "state/updated":
                state.update(event.payload.get("delta", {}))
        return state

    @classmethod
    def replay(cls, path: Path) -> SessionLedger:
        events: list[SessionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(SessionEvent(**json.loads(line)))
        if not events:
            raise ValueError("cannot replay an empty ledger")
        return cls(events[0].run_id, path=None, seed=events)
