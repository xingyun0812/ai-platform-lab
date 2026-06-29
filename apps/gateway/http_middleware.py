"""Gateway HTTP 中间件（Issue #156 PR-1）。"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request

from packages.observability.context import get_trace_id

if TYPE_CHECKING:
    from apps.gateway.settings import Settings

logger = logging.getLogger("ai_platform.gateway")


def register_gateway_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        trace_id = get_trace_id()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        tenant_id = request.headers.get("x-tenant-id")
        error_code = getattr(request.state, "audit_error_code", None)
        model = getattr(request.state, "audit_model", None)
        logger.info(
            "request",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
                "tenant_id": tenant_id,
            },
        )
        if settings.audit_enabled and request.url.path not in ("/healthz", "/metrics"):
            actor_role = getattr(request.state, "actor_role", None)
            try:
                from packages.audit.store import get_audit_store

                get_audit_store(settings.audit_db_path).insert(
                    tenant_id=tenant_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    latency_ms=elapsed_ms,
                    trace_id=trace_id,
                    model=model,
                    error_code=error_code,
                )
            except Exception:
                logger.exception("audit insert failed path=%s", request.url.path)
            if settings.audit_postgres_enabled:
                try:
                    from packages.audit.postgres_store import AuditPostgresStore
                    from packages.billing.db import get_effective_database_url

                    pg_url = get_effective_database_url(settings.database_url)
                    if pg_url:
                        AuditPostgresStore(pg_url).insert(
                            tenant_id=tenant_id,
                            actor_role=actor_role,
                            method=request.method,
                            path=request.url.path,
                            status_code=response.status_code,
                            latency_ms=elapsed_ms,
                            trace_id=trace_id,
                            model=model,
                            error_code=error_code,
                        )
                except Exception:
                    logger.exception("audit postgres insert failed path=%s", request.url.path)
        return response

    @app.middleware("http")
    async def region_context(request: Request, call_next):
        from apps.gateway.middleware.region import bind_region_context
        from packages.region.context import clear_request_region

        region_err = await bind_region_context(request)
        if region_err is not None:
            return region_err
        try:
            return await call_next(request)
        finally:
            clear_request_region()
