"""测试包 — unittest 入口自动绑定 InMemoryPlatformPort（pytest 见 conftest）。"""

from __future__ import annotations

from packages.platform import configure
from packages.platform._runtime import get_configured_port
from packages.platform.testing import InMemoryPlatformPort

if get_configured_port() is None:
    configure(InMemoryPlatformPort())
