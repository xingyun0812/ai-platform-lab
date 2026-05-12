from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from apps.gateway.llm_proxy import forward_chat_completions
from apps.gateway.quota import DailyQuotaTracker
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.errors import ErrorBody, ErrorDetail
from packages.contracts.schemas import ChatCompletionRequest
from packages.observability.context import get_trace_id
from packages.observability.middleware import TraceIdMiddleware

logger = logging.getLogger("ai_platform.gateway")

quota_tracker = DailyQuotaTracker()
_tenants_cache: dict[str, TenantRecord] | None = None


def get_tenants() -> dict[str, TenantRecord]:
    global _tenants_cache
    if _tenants_cache is None:
        _tenants_cache = load_tenants()
    return _tenants_cache


def json_error(
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    tid = get_trace_id()
    body = ErrorBody(
        error=ErrorDetail(
            code=code,
            message=message,
            trace_id=tid,
            detail=detail,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def resolve_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="missing X-Tenant-Id")
    token = parse_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing Authorization Bearer")

    tenant = tenants.get(x_tenant_id.strip())
    if not tenant:
        raise HTTPException(status_code=401, detail="unknown tenant")

    if token != tenant.bearer_token:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    return tenant


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(TraceIdMiddleware)

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

        payload = body.upstream_payload(settings.default_model)
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
