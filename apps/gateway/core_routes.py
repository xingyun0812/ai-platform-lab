"""Gateway 核心路由 — healthz / metrics（Issue #156 PR-1）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from packages.observability.telemetry_registry import prometheus_text as telemetry_prometheus_text

if TYPE_CHECKING:
    from apps.gateway.settings import Settings


def register_core_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        if not settings.metrics_enabled:
            return PlainTextResponse("# metrics disabled\n", status_code=503)
        return PlainTextResponse(
            telemetry_prometheus_text(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
