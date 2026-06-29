"""Worker PlatformPort 绑定 — 不依赖 apps.gateway.rag（Issue #152 PR-3）。"""

from __future__ import annotations


def wire_worker_platform() -> None:
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
