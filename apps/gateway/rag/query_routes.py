from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.quota import get_quota_tracker
from apps.gateway.rag.query_service import RagQueryRefusal, run_rag_query
from apps.gateway.request_guards import check_model_allowed, check_rate_limit
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.rag_schemas import RagQueryRequest, RagQueryResponse
from packages.observability.otel import component_span

logger = logging.getLogger("ai_platform.gateway.rag.query")

router = APIRouter(prefix="/v1/rag", tags=["rag"])
quota_tracker = get_quota_tracker()


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


@router.post("/query", response_model=RagQueryResponse)
async def rag_query(
    body: RagQueryRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if not x_tenant_id or body.tenant_id.strip() != x_tenant_id.strip():
        return json_error(
            400,
            "TENANT_MISMATCH",
            "body.tenant_id 须与请求头 X-Tenant-Id 一致",
        )

    if body.tenant_id.strip() != tenant.tenant_id:
        return json_error(403, "TENANT_FORBIDDEN", "tenant_id 与鉴权租户不一致")

    settings = get_settings()
    rate_err = check_rate_limit(tenant)
    if rate_err is not None:
        return rate_err

    model_err, _resolved_model = check_model_allowed(
        tenant,
        body.model or settings.rag_query_model,
    )
    if model_err is not None:
        return model_err

    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    if not quota_tracker.has_quota(tenant.tenant_id, tenant.daily_request_quota):
        return json_error(
            429,
            "QUOTA_EXCEEDED",
            "租户日配额已用尽",
            detail={"tenant_id": tenant.tenant_id},
        )

    try:
        with component_span(
            "rag.query",
            component="rag",
            enabled=settings.otel_enabled,
            kb_id=body.kb_id,
            tenant_id=tenant.tenant_id,
        ):
            result = await run_rag_query(
                kb_id=body.kb_id,
                version=body.version,
                query=body.query,
                top_k=body.top_k,
                min_score=body.min_score,
                model=body.model,
                tenant_id=tenant.tenant_id,
                daily_request_quota=tenant.daily_request_quota,
                quota_tracker=quota_tracker,
            )
    except RagQueryRefusal as e:
        if e.code == "QUOTA_EXCEEDED":
            return json_error(429, e.code, e.message, detail=e.detail)
        # 业务拒答：HTTP 422 + 统一 error.code（与成功 200 分离）
        return json_error(422, e.code, e.message, detail=e.detail)
    except Exception as e:
        logger.exception("rag_query failed tenant=%s kb=%s", tenant.tenant_id, body.kb_id)
        return json_error(503, "RAG_QUERY_ERROR", str(e))

    response = RagQueryResponse(tenant_id=tenant.tenant_id, **result)
    return JSONResponse(status_code=200, content=response.model_dump())
