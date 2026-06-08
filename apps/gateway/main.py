from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.gateway.agent.routes import router as agent_router
from apps.gateway.audit_routes import router as audit_router
from apps.gateway.billing_routes import router as billing_router
from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.model_router import forward_with_model_router
from apps.gateway.quota import get_quota_tracker
from apps.gateway.rag.query_routes import router as rag_query_router
from apps.gateway.rag.routes import router as rag_router
from apps.gateway.request_guards import check_model_allowed, check_rate_limit, check_token_budget
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.billing.recorder import record_upstream_usage
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
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(rag_router)
    app.include_router(rag_query_router)
    app.include_router(agent_router)
    app.include_router(audit_router)
    app.include_router(billing_router)

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

        rate_err = check_rate_limit(tenant)
        if rate_err is not None:
            return rate_err

        budget_err = check_token_budget(tenant)
        if budget_err is not None:
            return budget_err

        model_err, resolved_model = check_model_allowed(tenant, body.model)
        if model_err is not None:
            return model_err

        if not quota_tracker.try_consume(tenant.tenant_id, tenant.daily_request_quota):
            return json_error(
                429,
                "QUOTA_EXCEEDED",
                "租户日配额已用尽（UTC 日切重置）",
                detail={"tenant_id": tenant.tenant_id, "quota": tenant.daily_request_quota},
            )

        if not (settings.llm_api_key or "").strip():
            return json_error(
                503,
                "UPSTREAM_NOT_CONFIGURED",
                "LLM_API_KEY 未配置：申请到账号后写入项目根目录 .env 即可联调",
            )

        from packages.observability.otel import component_span

        payload = body.upstream_payload(resolved_model)
        with component_span(
            "gateway.chat_completions",
            component="gateway",
            enabled=settings.otel_enabled,
            tenant_id=tenant.tenant_id,
            model=resolved_model,
        ):
            routed = await forward_with_model_router(
                payload,
                requested_model=body.model,
                tenant_default=tenant.default_model,
            )

        if routed.error and routed.body is None:
            return json_error(
                503,
                "UPSTREAM_ERROR",
                routed.error,
                detail={
                    "upstream_status": routed.status,
                    "models_tried": list(routed.models_tried),
                },
            )

        if routed.body is None:
            return json_error(502, "UPSTREAM_ERROR", "empty upstream body")

        if not (200 <= routed.status < 300):
            return json_error(
                routed.status if 400 <= routed.status < 600 else 502,
                "UPSTREAM_ERROR",
                f"upstream status {routed.status}",
                detail={
                    "upstream": routed.body,
                    "models_tried": list(routed.models_tried),
                },
            )

        content = dict(routed.body)
        usage = record_upstream_usage(
            tenant_id=tenant.tenant_id,
            path="/v1/chat/completions",
            model=routed.model_used or resolved_model,
            upstream_body=routed.body,
            trace_id=get_trace_id(),
        )
        snap = get_budget_snapshot(
            tenant.tenant_id,
            token_budget_daily=tenant.token_budget_daily,
            token_budget_monthly=tenant.token_budget_monthly,
        )
        meta = content.setdefault("_platform", {})
        if isinstance(meta, dict):
            if routed.fallback_used and routed.model_used:
                meta["model_used"] = routed.model_used
                meta["fallback_used"] = True
                meta["models_tried"] = list(routed.models_tried)
            if usage is not None:
                meta["usage"] = {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    **budget_platform_meta(snap, usage.total_tokens),
                }
        return JSONResponse(status_code=200, content=content)

    return app


app = create_app()
