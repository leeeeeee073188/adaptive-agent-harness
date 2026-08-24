"""Context-local sink for tool action/verification audit facts."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ActionAuditSink = Callable[[Mapping[str, Any]], None]

_SINK: ContextVar[ActionAuditSink | None] = ContextVar("adaptive_action_audit_sink", default=None)


@contextmanager
def bind_action_audit_sink(sink: ActionAuditSink) -> Iterator[None]:
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def emit_action_audit(payload: Mapping[str, Any]) -> None:
    sink = _SINK.get()
    if sink is not None:
        sink(dict(payload))

