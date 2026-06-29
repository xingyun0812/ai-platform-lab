"""Pytest hooks — 测试会话绑定 packages.platform。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _wire_platform_for_tests() -> None:
    """每个用例前绑定 InMemoryPlatformPort（不依赖 apps.gateway 是否被 stub）。"""
    from packages.platform import configure, reset_platform_for_tests
    from packages.platform.testing import InMemoryPlatformPort

    reset_platform_for_tests()
    configure(InMemoryPlatformPort())
    yield  # type: ignore[misc]
    reset_platform_for_tests()
