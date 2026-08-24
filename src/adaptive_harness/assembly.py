"""Executable Profile assembly for Kernel plugin composition."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from adaptive_harness.config import PluginSpec, Profile
from adaptive_harness.kernel import Kernel, Plugin

type PluginFactory = Callable[[PluginSpec], Plugin]


@dataclass(frozen=True)
class PluginRegistry:
    """Closed registry that maps Profile PluginSpec names to plugin factories."""

    _factories: Mapping[str, PluginFactory]

    @classmethod
    def from_entries(cls, entries: Iterable[tuple[str, PluginFactory]]) -> PluginRegistry:
        factories: dict[str, PluginFactory] = {}
        for name, factory in entries:
            if name in factories:
                raise ValueError(f"duplicate plugin factory for {name!r}")
            factories[name] = factory
        return cls(factories)

    def get(self, spec: PluginSpec) -> PluginFactory:
        try:
            return self._factories[spec.name]
        except KeyError as exc:
            raise KeyError(f"unknown plugin {spec.name!r}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._factories)


async def assemble_profile(
    profile: Profile,
    registry: PluginRegistry,
    *,
    kernel: Kernel | None = None,
) -> Kernel:
    """Mount enabled Profile specs into a Kernel in deterministic compose order.

    The Profile remains the executable source of truth: each enabled
    ``PluginSpec`` is resolved by name, constructed by its registry factory, and
    mounted with ``spec.config``. Failures are fail-closed: unknown factories or
    mount errors close any plugin effects already installed during this assembly.
    """

    target = kernel or Kernel()
    mounted_by_assembly: list[str] = []
    try:
        for spec in profile.compose():
            plugin = registry.get(spec)(spec)
            if plugin.name != spec.name:
                raise ValueError(
                    f"plugin factory for {spec.name!r} returned {plugin.name!r}"
                )
            await target.mount(plugin, spec.config)
            mounted_by_assembly.append(plugin.name)
    except BaseException as error:
        cleanup_errors: list[BaseException] = []
        for name in reversed(mounted_by_assembly):
            try:
                await target.unmount(name)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            raise BaseExceptionGroup(
                "Profile assembly failed and cleanup reported errors",
                [error, *cleanup_errors],
            ) from None
        raise
    return target
