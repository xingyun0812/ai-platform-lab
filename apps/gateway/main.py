from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.llm_proxy import forward_chat_completions
from apps.gateway.quota import get_quota_tracker
from apps.gateway.agent.routes import router as agent_router
from apps.gateway.rag.query_routes import router as rag_query_router
from apps.gateway.rag.routes import router as rag_router
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.schemas import ChatCompletionRequest
from packages.observability.context import get_trace_id
from packages.observability.metrics import get_metrics_store
from packages.observability.middleware import TraceIdMiddleware
from packages.observability.otel import init_otel

logger = logging.getLogger("ai_platform.gateway")

quota_tracker = get_quota_tracker()
_tenants_cache: dict[str, TenantRecord] | None = None


def get_tenants() -> dict[str, TenantRecord]:
    global _tenants_cache
    if _tenants_cache is None:
        _tenants_cache = load_tenants()
    return _tenants_cache


def create_app() -> FastAPI:
    settings = get_settings()
    init_otel(
        service_name=settings.app_name,
        enabled=settings.otel_enabled,
        console_export=settings.otel_console_export,
    )
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(rag_router)
    app.include_router(rag_query_router)
    app.include_router(agent_router)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.perf_counter()
        trace_id = get_trace_id()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": round(elapsed_ms, 2),
            },
        )
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        if not settings.metrics_enabled:
            return PlainTextResponse("# metrics disabled\n", status_code=503)
        body = get_metrics_store().prometheus_text()
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        tenants = get_tenants()
        try:
            tenant = resolve_tenant(x_tenant_id, authorization, tenants)
        except HTTPException as e:
            return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))

        if body.stream:
            return json_error(
                400,
                "BAD_REQUEST",
                "当前骨架暂不支持 stream=true，请使用非流式",
            )

        if not (settings.llm_api_key or "").strip():
            return json_error(
                503,
                "UPSTREAM_NOT_CONFIGURED",
                "LLM_API_KEY 未配置：申请到账号后写入项目根目录 .env 即可联调",
            )

        resolved_model = body.model or settings.default_model
        if tenant.allowed_models and resolved_model not in tenant.allowed_models:
            return json_error(
                403,
                "MODEL_NOT_ALLOWED",
                f"模型不在租户白名单: {resolved_model}",
                detail={"allowed_models": list(tenant.allowed_models)},
            )

        if not quota_tracker.try_consume(tenant.tenant_id, tenant.daily_request_quota):
            return json_error(
                429,
                "QUOTA_EXCEEDED",
                "租户日配额已用尽（进程内计数，UTC 日切重置）",
                detail={"tenant_id": tenant.tenant_id, "quota": tenant.daily_request_quota},
            )

        from packages.observability.otel import component_span

        payload = body.upstream_payload(settings.default_model)
        with component_span(
            "gateway.chat_completions",
            component="gateway",
            enabled=settings.otel_enabled,
            tenant_id=tenant.tenant_id,
        ):
            status, upstream_json, err = await forward_chat_completions(payload)

        if err and upstream_json is None:
            return json_error(
                503,
                "UPSTREAM_ERROR",
                err,
                detail={"upstream_status": status},
            )

        if upstream_json is None:
            return json_error(502, "UPSTREAM_ERROR", "empty upstream body")

        if not (200 <= status < 300):
            return json_error(
                status if 400 <= status < 600 else 502,
                "UPSTREAM_ERROR",
                f"upstream status {status}",
                detail={"upstream": upstream_json},
            )

        return JSONResponse(status_code=200, content=upstream_json)

    return app


app = create_app()
