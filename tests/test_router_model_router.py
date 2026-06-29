"""packages.router.model_router 单测 — Issue #150 Phase 2。"""

from __future__ import annotations

import unittest

from packages.platform import configure, reset_platform_for_tests
from packages.platform.testing import InMemoryPlatformPort
from packages.router.model_router import (
    reset_model_router_config_for_tests,
    resolve_model_name,
)


class TestModelRouter(unittest.TestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()
        reset_model_router_config_for_tests()
        configure(InMemoryPlatformPort())

    def test_resolve_model_name_alias(self) -> None:
        resolved = resolve_model_name("chat-fast")
        self.assertEqual(resolved, "deepseek-v4-flash")

    def test_resolve_model_name_passthrough(self) -> None:
        resolved = resolve_model_name("gpt-4o-mini")
        self.assertEqual(resolved, "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
