from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from adaptive_harness.assembly import PluginRegistry, assemble_profile
from adaptive_harness.config import Bundle, PluginSpec, Profile
from adaptive_harness.kernel import PluginContext, ServiceKey

VALUE = ServiceKey[int]("assembly.value")
DERIVED = ServiceKey[int]("assembly.derived")


class ValuePlugin:
    name = "value"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        context.provide(VALUE, int(context.config["value"]))


class DerivedPlugin:
    name = "derived"
    requires = (VALUE,)

    async def mount(self, context: PluginContext) -> None:
        context.provide(DERIVED, context.services.get(VALUE) + int(context.config["delta"]))


class FailingPlugin:
    name = "failing"
    requires = ()

    def __init__(self, closed: list[str]) -> None:
        self._closed = closed

    async def mount(self, context: PluginContext) -> None:
        context.effect(lambda: self._closed.append("failing"))
        raise RuntimeError("boom")


class TrackedPlugin:
    requires = ()

    def __init__(self, name: str, closed: list[str]) -> None:
        self.name = name
        self._closed = closed

    async def mount(self, context: PluginContext) -> None:
        context.effect(lambda: self._closed.append(self.name))


class FailingDisposerPlugin:
    name = "bad-disposer"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        def dispose() -> None:
            raise RuntimeError("dispose failed")

        context.effect(dispose)


class ProfileAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_assembles_enabled_profile_plugins_in_deterministic_order(self) -> None:
        seen_specs: list[tuple[str, Mapping[str, Any]]] = []
        registry = PluginRegistry.from_entries(
            (
                ("value", lambda spec: seen_specs.append((spec.name, spec.config)) or ValuePlugin()),
                ("derived", lambda spec: seen_specs.append((spec.name, spec.config)) or DerivedPlugin()),
                ("disabled", lambda spec: self.fail("disabled specs must not be created")),
            )
        )
        profile = Profile(
            "candidate",
            (
                Bundle(
                    "base",
                    (
                        PluginSpec("value", {"value": 4}),
                        PluginSpec("disabled", enabled=False),
                        PluginSpec("derived", {"delta": 5}),
                    ),
                ),
            ),
        )

        kernel = await assemble_profile(profile, registry)

        self.assertEqual(kernel.mounted_plugins, ("value", "derived"))
        self.assertEqual(kernel.services.get(DERIVED), 9)
        self.assertEqual(seen_specs, [("value", {"value": 4}), ("derived", {"delta": 5})])
        await kernel.close()

    async def test_unknown_plugin_fails_closed_and_closes_already_mounted_plugins(self) -> None:
        closed: list[str] = []
        registry = PluginRegistry.from_entries(
            (("tracked", lambda spec: TrackedPlugin("tracked", closed)),)
        )
        profile = Profile(
            "candidate",
            (Bundle("base", (PluginSpec("tracked"), PluginSpec("missing"))),),
        )

        with self.assertRaisesRegex(KeyError, "missing"):
            await assemble_profile(profile, registry)

        self.assertEqual(closed, ["tracked"])

    async def test_duplicate_registry_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate plugin factory"):
            PluginRegistry.from_entries(
                (
                    ("value", lambda spec: ValuePlugin()),
                    ("value", lambda spec: ValuePlugin()),
                )
            )

    async def test_factory_cannot_substitute_a_different_plugin_name(self) -> None:
        registry = PluginRegistry.from_entries(
            (("declared", lambda spec: TrackedPlugin("substituted", [])),)
        )
        profile = Profile("candidate", (Bundle("base", (PluginSpec("declared"),)),))

        with self.assertRaisesRegex(ValueError, "returned 'substituted'"):
            await assemble_profile(profile, registry)

    async def test_partial_mount_failure_closes_plugin_effects_and_prior_plugins(self) -> None:
        closed: list[str] = []
        registry = PluginRegistry.from_entries(
            (
                ("first", lambda spec: TrackedPlugin("first", closed)),
                ("failing", lambda spec: FailingPlugin(closed)),
            )
        )
        profile = Profile(
            "candidate",
            (Bundle("base", (PluginSpec("first"), PluginSpec("failing"))),),
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await assemble_profile(profile, registry)

        self.assertEqual(closed, ["failing", "first"])

    async def test_cleanup_continues_after_one_disposer_fails(self) -> None:
        closed: list[str] = []
        registry = PluginRegistry.from_entries(
            (
                ("first", lambda spec: TrackedPlugin("first", closed)),
                ("bad-disposer", lambda spec: FailingDisposerPlugin()),
                ("failing", lambda spec: FailingPlugin(closed)),
            )
        )
        profile = Profile(
            "candidate",
            (
                Bundle(
                    "base",
                    (
                        PluginSpec("first"),
                        PluginSpec("bad-disposer"),
                        PluginSpec("failing"),
                    ),
                ),
            ),
        )

        with self.assertRaisesRegex(BaseExceptionGroup, "cleanup reported errors"):
            await assemble_profile(profile, registry)

        self.assertEqual(closed, ["failing", "first"])

    def test_profile_fingerprint_rejects_credentials(self) -> None:
        profile = Profile(
            "unsafe",
            (Bundle("base", (PluginSpec("model", {"api_key": "sk-secret"}),)),),
        )

        with self.assertRaisesRegex(ValueError, "credentials"):
            profile.fingerprint()

    def test_profile_fingerprint_rejects_prefixed_keys_and_secret_values(self) -> None:
        unsafe_configs = (
            {"openai_api_key": "value"},
            {"primary_access_token": "value"},
            {"provider": {"secret_key": "value"}},
            {"credential": "value"},
            {"endpoint": "sk-1234567890abcdef"},
        )
        for index, config in enumerate(unsafe_configs):
            with self.subTest(config=config):
                profile = Profile(
                    f"unsafe-{index}",
                    (Bundle("base", (PluginSpec("model", config),)),),
                )
                with self.assertRaisesRegex(ValueError, "credential"):
                    profile.fingerprint()

    def test_profile_fingerprint_is_stable_and_credential_free(self) -> None:
        profile = Profile(
            "safe",
            (Bundle("base", (PluginSpec("model", {"api_key_env": "DEEPSEEK_API_KEY"}),)),),
        )

        self.assertEqual(profile.fingerprint(), profile.fingerprint())
        self.assertNotIn("DEEPSEEK_API_KEY", profile.fingerprint())


if __name__ == "__main__":
    unittest.main()
