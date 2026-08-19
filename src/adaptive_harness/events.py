"""Typed event catalog and dispatch modes for live control seams."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventMode(StrEnum):
    EMIT = "emit"
    PARALLEL = "parallel"
    SERIAL = "serial"
    WATERFALL = "waterfall"


@dataclass(frozen=True)
class EventSpec:
    name: str
    mode: EventMode


type EventHandler = Callable[..., Any | Awaitable[Any]]
type NextHandler = Callable[[Any], Awaitable[Any]]


@dataclass(frozen=True)
class _Subscription:
    owner: str
    scope: str | None
    handler: EventHandler


class EventBus:
    def __init__(self, *, parent: EventBus | None = None, scope: str | None = None) -> None:
        self._parent = parent
        self._scope = scope
        self._subscriptions: dict[EventSpec, list[_Subscription]] = {}

    def child(self, scope: str) -> EventBus:
        return EventBus(parent=self, scope=scope)

    def subscribe(self, spec: EventSpec, handler: EventHandler, *, owner: str) -> Callable[[], None]:
        subscription = _Subscription(owner, self._scope, handler)
        self._subscriptions.setdefault(spec, []).append(subscription)

        def dispose() -> None:
            entries = self._subscriptions.get(spec, [])
            if subscription in entries:
                entries.remove(subscription)

        return dispose

    def _listeners(self, spec: EventSpec) -> list[_Subscription]:
        listeners = self._parent._listeners(spec) if self._parent is not None else []
        listeners.extend(self._subscriptions.get(spec, []))
        return listeners

    async def dispatch(self, spec: EventSpec, payload: Any) -> Any:
        listeners = self._listeners(spec)
        if spec.mode is EventMode.PARALLEL:
            await asyncio.gather(*(self._call(item.handler, payload) for item in listeners))
            return payload
        if spec.mode is EventMode.EMIT:
            for item in listeners:
                await self._call(item.handler, payload)
            return payload
        if spec.mode is EventMode.SERIAL:
            current = payload
            for item in listeners:
                result = await self._call(item.handler, current)
                if result is not None:
                    current = result
            return current
        if spec.mode is EventMode.WATERFALL:
            return await self._waterfall(listeners, 0, payload)
        raise AssertionError(f"unsupported event mode: {spec.mode}")

    async def _waterfall(self, listeners: list[_Subscription], index: int, payload: Any) -> Any:
        if index >= len(listeners):
            return payload

        async def next_handler(next_payload: Any = payload) -> Any:
            return await self._waterfall(listeners, index + 1, next_payload)

        return await self._call(listeners[index].handler, payload, next_handler)

    @staticmethod
    async def _call(handler: EventHandler, *args: Any) -> Any:
        result = handler(*args)
        return await result if isinstance(result, Awaitable) else result
