"""Gateway Chat Completions 路由（Issue #156 PR-1）。"""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.quota import get_quota_tracker
from apps.gateway.request_guards import check_model_allowed, check_rate_limit, check_token_budget
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.billing.recorder import record_upstream_usage
from packages.contracts.schemas import ChatCompletionRequest
from packages.observability.context import get_trace_id
from packages.observability.otel import component_span
from packages.router.model_router import forward_with_model_router
from packages.semantic_cache import get_semantic_cache

logger = logging.getLogger("ai_platform.gateway.chat")

router = APIRouter(tags=["chat"])
quota_tracker = get_quota_tracker()


def get_tenants() -> dict[str, TenantRecord]:
    return load_tenants()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    settings = get_settings()
    tenants = get_tenants()
    try:
        tenant = resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))

    request.state.actor_role = tenant.role

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

    payload = body.upstream_payload(resolved_model)

    cache = get_semantic_cache()
    cache_lookup = None
    if cache is not None:
        cache_lookup = await cache.lookup(
            tenant_id=tenant.tenant_id,
            model=resolved_model,
            messages=[m.model_dump() for m in body.messages],
            temperature=body.temperature,
            stream=bool(body.stream),
        )
        if isinstance(cache_lookup, str):
            logger.debug("semantic cache skipped: %s", cache_lookup)
            cache_lookup = None
        elif cache_lookup is not None:
            cached_body = dict(cache_lookup.entry.response)
            meta = cached_body.setdefault("_platform", {})
            if isinstance(meta, dict):
                meta["cache_hit"] = True
                meta["cache_mode"] = cache_lookup.mode
                meta["cache_similarity"] = round(cache_lookup.similarity, 4)
                meta["cache_age_seconds"] = round(time.time() - cache_lookup.entry.created_at, 2)
                meta["model"] = cache_lookup.entry.model
                meta["tenant_id"] = tenant.tenant_id
            logger.info(
                "semantic cache hit tenant=%s model=%s mode=%s sim=%.4f",
                tenant.tenant_id,
                resolved_model,
                cache_lookup.mode,
                cache_lookup.similarity,
            )
            return JSONResponse(status_code=200, content=cached_body)

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
        code = "CIRCUIT_OPEN" if "熔断" in (routed.error or "") else "UPSTREAM_ERROR"
        return json_error(
            503,
            code,
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
        if routed.provider_id:
            meta["provider_id"] = routed.provider_id
        if usage is not None:
            meta["usage"] = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                **budget_platform_meta(snap, usage.total_tokens),
            }

    if cache is not None and cache_lookup is None:
        try:
            await cache.store(
                tenant_id=tenant.tenant_id,
                model=resolved_model,
                messages=[m.model_dump() for m in body.messages],
                response=content,
                usage_tokens=(usage.total_tokens if usage else 0),
                temperature=body.temperature,
                stream=bool(body.stream),
            )
        except Exception:
            logger.exception("semantic cache store failed")

    return JSONResponse(status_code=200, content=content)


def register_chat_routes(app: FastAPI) -> None:
    app.include_router(router)
