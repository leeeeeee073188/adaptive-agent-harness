"""Context-local sink used by runtime adapters to ledger model-surface decisions."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ContextAuditSink = Callable[[Mapping[str, Any]], None]

_SINK: ContextVar[ContextAuditSink | None] = ContextVar("adaptive_context_audit_sink", default=None)


@contextmanager
def bind_context_audit_sink(sink: ContextAuditSink) -> Iterator[None]:
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def emit_context_audit(payload: Mapping[str, Any]) -> None:
    sink = _SINK.get()
    if sink is not None:
        sink(dict(payload))

