"""Eval 脚本共用 — 在直接 import packages 前绑定 PlatformPort。"""

from __future__ import annotations


def ensure_platform_wired() -> None:
    from packages.platform import configure
    from packages.platform._runtime import get_configured_port

    if get_configured_port() is not None:
        return
    try:
        from apps.gateway.platform_adapter import wire_platform

        wire_platform()
    except Exception:
        from packages.platform.testing import InMemoryPlatformPort

        configure(InMemoryPlatformPort())
