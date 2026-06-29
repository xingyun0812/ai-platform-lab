"""packages/platform 边界测试 — InMemoryPlatformPort + configure。"""

from __future__ import annotations

import unittest

from packages.platform import (
    configure,
    forward_with_model_router,
    get_settings,
    is_model_allowed,
    reset_platform_for_tests,
    resolve_retrieve_version,
    resolve_source_path,
)
from packages.platform.testing import InMemoryPlatformPort, InMemoryPlatformSettings


class TestPlatformPort(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()

    def tearDown(self) -> None:
        reset_platform_for_tests()

    def test_unconfigured_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            get_settings()

    def test_in_memory_settings(self) -> None:
        port = InMemoryPlatformPort(
            settings=InMemoryPlatformSettings(default_model="demo-model"),
        )
        configure(port)
        settings = get_settings()
        self.assertEqual(settings.default_model, "demo-model")

    async def test_in_memory_forward(self) -> None:
        port = InMemoryPlatformPort()
        configure(port)
        result = await forward_with_model_router({"messages": []}, requested_model="m1")
        self.assertEqual(result["model_used"], "m1")

    def test_is_model_allowed(self) -> None:
        port = InMemoryPlatformPort(resolved_model="chat-fast")
        configure(port)
        ok, resolved = is_model_allowed(
            "chat-fast",
            tenant_default=None,
            allowed_models=("chat-fast",),
        )
        self.assertTrue(ok)
        self.assertEqual(resolved, "chat-fast")

    def test_resolve_helpers(self) -> None:
        port = InMemoryPlatformPort(
            settings=InMemoryPlatformSettings(rag_data_root=__import__("pathlib").Path("/data/rag")),
            retrieve_versions={"kb1": 3},
        )
        configure(port)
        path = resolve_source_path("docs/a.txt")
        self.assertTrue(str(path).endswith("docs/a.txt"))
        self.assertEqual(resolve_retrieve_version("kb1", None), 3)
        self.assertEqual(resolve_retrieve_version("kb1", 7), 7)


class TestGatewayPlatformAdapter(unittest.TestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()

    def tearDown(self) -> None:
        reset_platform_for_tests()

    def test_wire_platform_configures_adapter(self) -> None:
        from apps.gateway.platform_adapter import wire_platform

        wire_platform()
        settings = get_settings()
        self.assertTrue(hasattr(settings, "default_model"))


if __name__ == "__main__":
    unittest.main()
