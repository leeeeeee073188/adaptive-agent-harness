"""Translate DeerFlow stream events into the canonical session ledger.

DeerFlow emits both incremental ``messages-tuple`` events and cumulative
``values`` snapshots. The adapter owns their reconciliation so the kernel and
evaluators consume one stable event vocabulary.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from adaptive_harness.capabilities import Environment
from adaptive_harness.ledger import SessionLedger

_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens")


@dataclass(frozen=True)
class DeerFlowReplaySummary:
    response_text: str
    tool_calls: tuple[Mapping[str, Any], ...]
    tool_results: tuple[Mapping[str, Any], ...]
    usage: Mapping[str, int]
    source_event_count: int
    canonical_event_count: int


@dataclass(frozen=True)
class DeerFlowRunRequest:
    """Non-secret inputs that determine one embedded DeerFlow run."""

    message: str
    thread_id: str
    client_options: Mapping[str, Any] = field(default_factory=dict)
    tool_schemas: Sequence[Mapping[str, Any]] = ()
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeerFlowRunResult:
    run_id: str
    ledger: SessionLedger
    summary: DeerFlowReplaySummary


class DeerFlowClient(Protocol):
    """Structural subset of the official embedded DeerFlowClient API."""

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs: Any,
    ) -> Iterable[Any]: ...


class DeerFlowRuntimeAdapter:
    """Thin runtime provider around an embedded DeerFlowClient instance."""

    def __init__(self, client: DeerFlowClient, environment: Environment) -> None:
        self.client = client
        self.environment = environment

    async def run(
        self,
        request: DeerFlowRunRequest,
        *,
        run_id: str | None = None,
        ledger_path: Path | None = None,
    ) -> DeerFlowRunResult:
        run_id = run_id or str(uuid.uuid4())
        ledger = SessionLedger(run_id, ledger_path)
        adapter = DeerFlowEventAdapter()
        _reject_secret_options(request.client_options)
        try:
            await self.environment.build()
            ledger.append(
                "request/header",
                {
                    "messages": [{"role": "user", "content": request.message}],
                    "tools": [dict(schema) for schema in request.tool_schemas],
                    "context": {
                        "run_id": run_id,
                        "thread_id": request.thread_id,
                        "environment": dict(self.environment.state()),
                        **dict(request.context),
                    },
                    "runtime": "deerflow",
                    "client_options": dict(request.client_options),
                },
                turn=1,
                step=1,
            )
            for raw_event in self.client.stream(
                request.message,
                thread_id=request.thread_id,
                **dict(request.client_options),
            ):
                adapter.append(ledger, _event_mapping(raw_event))
            summary = adapter.finish(ledger)
            return DeerFlowRunResult(run_id, ledger, summary)
        except Exception as error:
            ledger.append(
                "runtime/error",
                {"type": type(error).__name__, "message": str(error), "source": "deerflow"},
            )
            adapter.finish(ledger)
            raise
        finally:
            await self.environment.cleanup()


class DeerFlowEventAdapter:
    """Stateful, idempotent adapter for one DeerFlow run."""

    def __init__(self, *, include_chunks: bool = True) -> None:
        self.include_chunks = include_chunks
        self._source_event_count = 0
        self._response_chunks: dict[str, list[str]] = {}
        self._complete_responses: dict[str, str] = {}
        self._response_order: list[str] = []
        self._seen_messages: set[str] = set()
        self._seen_tool_calls: set[str] = set()
        self._seen_tool_results: set[str] = set()
        self._tool_calls: list[Mapping[str, Any]] = []
        self._tool_results: list[Mapping[str, Any]] = []
        self._usage: dict[str, int] = {}
        self._usage_message_ids: set[str] = set()
        self._final_usage: dict[str, int] | None = None
        self._saw_end = False
        self._finished = False

    def append(self, ledger: SessionLedger, event: Mapping[str, Any]) -> None:
        """Append canonical facts for one serialized DeerFlow event."""

        if self._finished:
            raise RuntimeError("cannot append DeerFlow events after finish")
        index = self._source_event_count
        self._source_event_count += 1
        event_type = str(event.get("type", "unknown"))
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}

        if event_type == "messages-tuple":
            self._append_message_tuple(ledger, data, index)
        elif event_type == "values":
            self._append_values(ledger, data, index)
        elif event_type == "end":
            self._append_end(ledger, data)
        else:
            ledger.append(f"runtime/deerflow/{event_type}", {"data": dict(data)})

    def replay(
        self,
        ledger: SessionLedger,
        events: Iterable[Mapping[str, Any]],
    ) -> DeerFlowReplaySummary:
        """Translate all events and return their deterministic projection."""

        start_count = len(ledger.events)
        for event in events:
            self.append(ledger, event)

        summary = self.finish(ledger)
        return DeerFlowReplaySummary(
            response_text=summary.response_text,
            tool_calls=summary.tool_calls,
            tool_results=summary.tool_results,
            usage=summary.usage,
            source_event_count=summary.source_event_count,
            canonical_event_count=len(ledger.events) - start_count,
        )

    def finish(self, ledger: SessionLedger) -> DeerFlowReplaySummary:
        """Finalize a complete or interrupted stream exactly once."""

        if not self._finished and not self._saw_end:
            ledger.append(
                "runtime/end",
                {"usage": dict(self._usage), "source": "deerflow-recovered"},
            )
        self._finished = True

        return DeerFlowReplaySummary(
            response_text=self._response_text(),
            tool_calls=tuple(self._tool_calls),
            tool_results=tuple(self._tool_results),
            usage=dict(self._final_usage if self._final_usage is not None else self._usage),
            source_event_count=self._source_event_count,
            canonical_event_count=len(ledger.events),
        )

    def _append_message_tuple(
        self,
        ledger: SessionLedger,
        data: Mapping[str, Any],
        index: int,
    ) -> None:
        kind = data.get("type")
        if kind == "ai":
            message_id = str(data.get("id") or f"event-{index}")
            content = _content(data.get("content"))
            self._remember_response_id(message_id)
            if content:
                self._response_chunks.setdefault(message_id, []).append(content)
            self._merge_usage(data.get("usage_metadata") or data.get("usage"), message_id)
            if self.include_chunks:
                ledger.append(
                    "assistant/chunk",
                    {"message_id": message_id, "content": content, "source": "deerflow"},
                )
            self._append_tool_calls(ledger, data.get("tool_calls"), f"deerflow-event:{index}")
        elif kind == "tool":
            self._append_tool_result(ledger, data, f"deerflow-event:{index}")
        else:
            ledger.append(
                "runtime/deerflow/messages-tuple",
                {"data": dict(data), "source_index": index},
            )

    def _append_values(
        self,
        ledger: SessionLedger,
        data: Mapping[str, Any],
        index: int,
    ) -> None:
        self._merge_usage(data.get("usage"))
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return
        for position, message in enumerate(raw_messages):
            if not isinstance(message, Mapping):
                continue
            kind = str(message.get("type", "unknown"))
            message_id = _message_key(message, kind, position)
            source = f"deerflow-values:{index}"

            if kind == "ai":
                self._remember_response_id(message_id)
                content = _content(message.get("content"))
                self._complete_responses[message_id] = content
                self._merge_usage(
                    message.get("usage_metadata") or message.get("usage"),
                    message_id,
                )
                if message_id not in self._seen_messages:
                    self._seen_messages.add(message_id)
                    ledger.append(
                        "assistant/message",
                        {"message_id": message_id, "content": content, "source": source},
                    )
                self._append_tool_calls(ledger, message.get("tool_calls"), source)
            elif kind == "tool":
                self._append_tool_result(ledger, message, source)
            elif kind == "human":
                if message_id not in self._seen_messages:
                    self._seen_messages.add(message_id)
                    ledger.append(
                        "user/message",
                        {
                            "message_id": message_id,
                            "content": _content(message.get("content")),
                            "source": source,
                        },
                    )
            elif kind == "system":
                if message_id not in self._seen_messages:
                    self._seen_messages.add(message_id)
                    ledger.append(
                        "runtime/system-message",
                        {
                            "message_id": message_id,
                            "content": _content(message.get("content")),
                            "source": source,
                        },
                    )
            elif message_id not in self._seen_messages:
                self._seen_messages.add(message_id)
                ledger.append(
                    f"runtime/deerflow/message/{kind}",
                    {"message": dict(message), "source": source},
                )

    def _append_tool_calls(self, ledger: SessionLedger, raw: Any, source: str) -> None:
        if not isinstance(raw, list):
            return
        for call in raw:
            if not isinstance(call, Mapping) or not call.get("id"):
                continue
            call_id = str(call["id"])
            if call_id in self._seen_tool_calls:
                continue
            self._seen_tool_calls.add(call_id)
            normalized = {
                name: value
                for name, value in call.items()
                if value not in (None, "", {})
            }
            normalized["_source"] = source
            self._tool_calls.append(normalized)
            ledger.append(
                "tool/call",
                {
                    "call_id": call_id,
                    "name": call.get("name"),
                    "args": call.get("args") or {},
                    "source": source,
                },
            )

    def _append_tool_result(
        self,
        ledger: SessionLedger,
        data: Mapping[str, Any],
        source: str,
    ) -> None:
        call_id = data.get("tool_call_id") or data.get("toolCallId")
        key = str(call_id or data.get("id") or _stable_key(data))
        if key in self._seen_tool_results:
            return
        self._seen_tool_results.add(key)
        result = {
            "toolCallId": call_id,
            "toolName": data.get("name") or data.get("tool_name"),
            "content": _content(data.get("content")),
            "artifact": data.get("artifact"),
            "_source": source,
        }
        self._tool_results.append(result)
        ledger.append(
            "tool/result",
            {
                "call_id": str(call_id or key),
                "name": result["toolName"],
                "content": result["content"],
                "artifact": result["artifact"],
                "source": source,
            },
        )

    def _append_end(self, ledger: SessionLedger, data: Mapping[str, Any]) -> None:
        self._saw_end = True
        self._final_usage = _usage(data.get("usage"))
        ledger.append(
            "runtime/end",
            {"usage": dict(self._final_usage), "source": "deerflow"},
        )

    def _merge_usage(self, raw: Any, message_id: str | None = None) -> None:
        if message_id is not None and message_id in self._usage_message_ids:
            return
        normalized = _usage(raw)
        if not normalized:
            return
        if message_id is not None:
            self._usage_message_ids.add(message_id)
        for key, value in normalized.items():
            self._usage[key] = self._usage.get(key, 0) + value

    def _remember_response_id(self, message_id: str) -> None:
        if message_id not in self._response_order:
            self._response_order.append(message_id)

    def _response_text(self) -> str:
        if not self._response_order:
            return ""
        message_id = self._response_order[-1]
        response = self._complete_responses.get(
            message_id,
            "".join(self._response_chunks.get(message_id, [])),
        )
        return response[-60000:]


def _usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    return {key: raw[key] for key in _USAGE_KEYS if isinstance(raw.get(key), int)}


def _content(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    return json.dumps(raw, ensure_ascii=False, default=str)


def _message_key(message: Mapping[str, Any], kind: str, position: int) -> str:
    explicit = message.get("id") or message.get("tool_call_id") or message.get("toolCallId")
    return str(explicit or f"{kind}:{position}:{_stable_key(message)}")


def _stable_key(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _event_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    event_type = getattr(raw, "type", None)
    data = getattr(raw, "data", None)
    if event_type is None:
        raise TypeError(f"unsupported DeerFlow event: {type(raw).__name__}")
    return {
        "type": str(event_type),
        "data": dict(data) if isinstance(data, Mapping) else {},
    }


def _reject_secret_options(options: Mapping[str, Any]) -> None:
    secret_keys = {"api_key", "apikey", "authorization", "access_token", "password", "secret"}

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower().replace("-", "_") in secret_keys:
                    raise ValueError(
                        f"client_options must reference credentials through the environment, not {key!r}"
                    )
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(options)
