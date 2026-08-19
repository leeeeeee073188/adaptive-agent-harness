from __future__ import annotations

import unittest

from adaptive_harness.kernel import Kernel, PluginContext, ServiceKey

VALUE = ServiceKey[int]("value")
DERIVED = ServiceKey[int]("derived")


class ValuePlugin:
    name = "value"
    requires = ()

    async def mount(self, context: PluginContext) -> None:
        context.provide(VALUE, int(context.config.get("value", 3)))


class DerivedPlugin:
    name = "derived"
    requires = (VALUE,)

    async def mount(self, context: PluginContext) -> None:
        context.provide(DERIVED, context.services.get(VALUE) * 2)


class KernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_and_reverse_unmount(self) -> None:
        kernel = Kernel()
        await kernel.mount(ValuePlugin(), {"value": 4})
        await kernel.mount(DerivedPlugin())
        self.assertEqual(kernel.services.get(DERIVED), 8)

        await kernel.close()

        self.assertFalse(kernel.services.has(VALUE))
        self.assertFalse(kernel.services.has(DERIVED))

    async def test_missing_dependency_fails_before_mount(self) -> None:
        kernel = Kernel()
        with self.assertRaisesRegex(RuntimeError, "value"):
            await kernel.mount(DerivedPlugin())

    async def test_child_scope_can_override_parent_service(self) -> None:
        parent = Kernel()
        await parent.mount(ValuePlugin(), {"value": 2})
        child = parent.child("agent-1")
        await child.mount(ValuePlugin(), {"value": 9})

        self.assertEqual(parent.services.get(VALUE), 2)
        self.assertEqual(child.services.get(VALUE), 9)
