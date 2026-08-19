"""Small plugin kernel with scoped services and reversible effects.

The design follows the architectural lesson of DeepSeek Harness/Cordis without
embedding Cordis itself: plugins depend on stable service keys, not concrete
implementations, and every registration is unwound when its owner unmounts.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from adaptive_harness.events import EventBus, EventHandler, EventSpec

T = TypeVar("T")
Disposer = Callable[[], None | Awaitable[None]]


@dataclass(frozen=True)
class ServiceKey[T]:
    """Stable, typed capability identity."""

    name: str


class ServiceRegistry:
    def __init__(self, parent: ServiceRegistry | None = None) -> None:
        self._parent = parent
        self._services: dict[ServiceKey[Any], tuple[str, Any]] = {}

    def child(self) -> ServiceRegistry:
        return ServiceRegistry(self)

    def provide(self, key: ServiceKey[T], value: T, *, owner: str) -> Disposer:
        if key in self._services:
            current_owner = self._services[key][0]
            raise ValueError(f"service {key.name!r} already provided by {current_owner!r} in this scope")
        self._services[key] = (owner, value)

        def dispose() -> None:
            current = self._services.get(key)
            if current is not None and current[0] == owner:
                del self._services[key]

        return dispose

    def get(self, key: ServiceKey[T]) -> T:
        current: ServiceRegistry | None = self
        while current is not None:
            entry = current._services.get(key)
            if entry is not None:
                return entry[1]
            current = current._parent
        raise KeyError(f"service {key.name!r} is not available")

    def has(self, key: ServiceKey[Any]) -> bool:
        try:
            self.get(key)
        except KeyError:
            return False
        return True

    def snapshot(self) -> Mapping[str, str]:
        values: dict[str, str] = {}
        if self._parent is not None:
            values.update(self._parent.snapshot())
        values.update({key.name: owner for key, (owner, _) in self._services.items()})
        return values


@runtime_checkable
class Plugin(Protocol):
    name: str
    requires: tuple[ServiceKey[Any], ...]

    async def mount(self, context: PluginContext) -> None: ...


@dataclass
class PluginContext:
    owner: str
    services: ServiceRegistry
    events: EventBus
    config: Mapping[str, Any]
    _effects: list[Disposer] = field(default_factory=list)

    def provide(self, key: ServiceKey[T], value: T) -> T:
        self.effect(self.services.provide(key, value, owner=self.owner))
        return value

    def subscribe(self, spec: EventSpec, handler: EventHandler) -> None:
        self.effect(self.events.subscribe(spec, handler, owner=self.owner))

    def effect(self, disposer: Disposer) -> None:
        self._effects.append(disposer)

    async def dispose(self) -> None:
        errors: list[BaseException] = []
        while self._effects:
            disposer = self._effects.pop()
            try:
                result = disposer()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:  # disposal must continue for later effects
                errors.append(exc)
        if errors:
            raise ExceptionGroup(f"plugin {self.owner!r} disposal failed", errors)


class Kernel:
    """Owns one composition scope and its mounted plugin lifecycle."""

    def __init__(
        self,
        *,
        services: ServiceRegistry | None = None,
        events: EventBus | None = None,
        scope: str = "app",
    ) -> None:
        self.services = services or ServiceRegistry()
        self.events = events or EventBus()
        self.scope = scope
        self._contexts: dict[str, PluginContext] = {}
        self._mount_order: list[str] = []

    def child(self, scope: str) -> Kernel:
        return Kernel(services=self.services.child(), events=self.events.child(scope), scope=scope)

    async def mount(self, plugin: Plugin, config: Mapping[str, Any] | None = None) -> None:
        if plugin.name in self._contexts:
            raise ValueError(f"plugin {plugin.name!r} is already mounted")
        missing = [key.name for key in plugin.requires if not self.services.has(key)]
        if missing:
            raise RuntimeError(f"plugin {plugin.name!r} is missing services: {', '.join(missing)}")
        context = PluginContext(plugin.name, self.services, self.events, config or {})
        try:
            await plugin.mount(context)
        except BaseException:
            await context.dispose()
            raise
        self._contexts[plugin.name] = context
        self._mount_order.append(plugin.name)

    async def unmount(self, name: str) -> None:
        context = self._contexts.pop(name)
        self._mount_order.remove(name)
        await context.dispose()

    async def close(self) -> None:
        errors: list[BaseException] = []
        for name in reversed(self._mount_order.copy()):
            try:
                await self.unmount(name)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("kernel shutdown failed", errors)

    @property
    def mounted_plugins(self) -> tuple[str, ...]:
        return tuple(self._mount_order)
