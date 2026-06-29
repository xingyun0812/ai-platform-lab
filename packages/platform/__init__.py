"""packages/platform — 切断 packages→gateway 反向依赖的门面（Issue #145 PR-1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.platform._runtime import _require_port, configure, reset_platform_for_tests
from packages.platform.types import PlatformPort, PlatformSettings

__all__ = [
    "PlatformPort",
    "PlatformSettings",
    "configure",
    "forward_with_model_router",
    "get_settings",
    "is_model_allowed",
    "reset_platform_for_tests",
    "resolve_retrieve_version",
    "resolve_source_path",
]


def get_settings() -> PlatformSettings:
    return _require_port().get_settings()


async def forward_with_model_router(
    payload: dict[str, Any],
    *,
    requested_model: str | None = None,
    tenant_default: str | None = None,
) -> Any:
    return await _require_port().forward_with_model_router(
        payload,
        requested_model=requested_model,
        tenant_default=tenant_default,
    )


def is_model_allowed(
    requested: str | None,
    *,
    tenant_default: str | None,
    allowed_models: tuple[str, ...] = (),
) -> tuple[bool, str]:
    return _require_port().is_model_allowed(
        requested,
        tenant_default=tenant_default,
        allowed_models=allowed_models,
    )


def resolve_source_path(source_uri: str) -> Path:
    return _require_port().resolve_source_path(source_uri)


def resolve_retrieve_version(kb_id: str, version: int | None) -> int:
    return _require_port().resolve_retrieve_version(kb_id, version)
