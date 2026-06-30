"""Gateway 核心路由 — healthz / metrics（Issue #156 PR-1）。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from packages.observability.metrics import get_metrics_store

if TYPE_CHECKING:
    from apps.gateway.settings import Settings

logger = logging.getLogger("ai_platform.gateway")


def register_core_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        if not settings.metrics_enabled:
            return PlainTextResponse("# metrics disabled\n", status_code=503)
        parts = [get_metrics_store().prometheus_text()]
        for loader, label in (
            ("packages.semantic_cache", "semantic cache metrics"),
            ("packages.memory", "memory metrics"),
            ("packages.rag.index_metrics", "rag index metrics"),
            ("packages.agent.perf_metrics", "agent perf metrics"),
        ):
            try:
                if loader == "packages.semantic_cache":
                    from packages.semantic_cache import get_semantic_cache_metrics

                    parts.append(get_semantic_cache_metrics().prometheus_text())
                elif loader == "packages.memory":
                    from packages.memory import get_memory_metrics

                    parts.append(get_memory_metrics().prometheus_text())
                elif loader == "packages.rag.index_metrics":
                    from packages.rag.index_metrics import get_index_metrics

                    parts.append(get_index_metrics().prometheus_text())
                else:
                    from packages.agent.perf_metrics import get_agent_perf_metrics

                    parts.append(get_agent_perf_metrics().prometheus_text())
            except Exception:
                logger.exception("%s export failed", label)
        return PlainTextResponse("".join(parts), media_type="text/plain; version=0.0.4; charset=utf-8")
