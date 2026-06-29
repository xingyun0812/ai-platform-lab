"""Gateway → packages.platform 薄适配器（Issue #145 PR-1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.gateway.model_router import (
    ModelRouteResult,
)
from apps.gateway.model_router import (
    forward_with_model_router as _forward_with_model_router,
)
from apps.gateway.model_router import (
    is_model_allowed as _is_model_allowed,
)
from apps.gateway.rag.paths import resolve_source_path as _resolve_source_path
from apps.gateway.rag.pipeline import resolve_retrieve_version as _resolve_retrieve_version
from apps.gateway.settings import Settings
from apps.gateway.settings import get_settings as _get_settings
from packages.platform.types import PlatformPort, PlatformSettings


class GatewayPlatformAdapter(PlatformPort):
    """将现有 gateway settings / model_router / rag paths 委托给 packages.platform。"""

    def get_settings(self) -> PlatformSettings:
        settings = _get_settings()
        assert isinstance(settings, Settings)
        return settings

    async def forward_with_model_router(
        self,
        payload: dict[str, Any],
        *,
        requested_model: str | None = None,
        tenant_default: str | None = None,
    ) -> ModelRouteResult:
        return await _forward_with_model_router(
            payload,
            requested_model=requested_model,
            tenant_default=tenant_default,
        )

    def is_model_allowed(
        self,
        requested: str | None,
        *,
        tenant_default: str | None,
        allowed_models: tuple[str, ...] = (),
    ) -> tuple[bool, str]:
        return _is_model_allowed(
            requested,
            tenant_default=tenant_default,
            allowed_models=allowed_models,
        )

    def resolve_source_path(self, source_uri: str) -> Path:
        return _resolve_source_path(source_uri)

    def resolve_retrieve_version(self, kb_id: str, version: int | None) -> int:
        return _resolve_retrieve_version(kb_id, version)


def wire_platform() -> None:
    """在 gateway / worker 入口绑定 PlatformPort。"""
    from packages.platform import configure

    configure(GatewayPlatformAdapter())
