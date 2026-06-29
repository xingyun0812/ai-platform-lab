"""Platform runtime binding — configure() 注入 Gateway 或 InMemory 端口。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.platform.types import PlatformPort

_port: PlatformPort | None = None


def configure(port: PlatformPort | None) -> None:
    global _port
    _port = port


def get_configured_port() -> PlatformPort | None:
    return _port


def _require_port() -> PlatformPort:
    if _port is None:
        raise RuntimeError(
            "packages.platform 未 configure；请在 create_app / 测试 setUp 调用 "
            "packages.platform.configure(...)"
        )
    return _port


def reset_platform_for_tests() -> None:
    global _port
    _port = None
