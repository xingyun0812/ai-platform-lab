from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.quota import get_quota_tracker
from apps.gateway.request_guards import check_rate_limit
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.runner import AgentRunError, run_agent
from packages.agent.session import get_session_store
from packages.contracts.agent_schemas import AgentRunRequest, AgentRunResponse
from packages.observability.otel import component_span

logger = logging.getLogger("ai_platform.gateway.agent")

router = APIRouter(prefix="/v1/agent", tags=["agent"])
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


@router.post("/run", response_model=AgentRunResponse)
async def agent_run(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if not x_tenant_id or body.tenant_id.strip() != x_tenant_id.strip():
        return json_error(400, "TENANT_MISMATCH", "body.tenant_id 须与 X-Tenant-Id 一致")

    settings = get_settings()
    rate_err = check_rate_limit(tenant)
    if rate_err is not None:
        return rate_err

    if not quota_tracker.has_quota(tenant.tenant_id, tenant.daily_request_quota):
        return json_error(429, "QUOTA_EXCEEDED", "租户日配额已用尽")

    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    new_messages: list[dict[str, Any]] = [
        m.model_dump(exclude_none=True) for m in body.messages
    ]
    if body.kb_id:
        hint = (
            f"默认知识库 kb_id={body.kb_id}。"
            "调用 get_kb_snippet 时请使用该 kb_id（除非用户指定其他库）。"
        )
        new_messages = [
            {"role": "system", "content": hint},
            *new_messages,
        ]

    if not quota_tracker.try_consume(tenant.tenant_id, tenant.daily_request_quota):
        return json_error(429, "QUOTA_EXCEEDED", "租户日配额已用尽")

    try:
        with component_span(
            "agent.run",
            component="agent",
            enabled=settings.otel_enabled,
            tenant_id=tenant.tenant_id,
            session_id=body.session_id.strip(),
        ):
            result = await run_agent(
                tenant_id=tenant.tenant_id,
                session_id=body.session_id.strip(),
                new_messages=new_messages,
                allowed_tools=tenant.allowed_tools,
                allowed_models=tenant.allowed_models,
                model=body.model,
                session_store=get_session_store(),
            )
    except AgentRunError as e:
        if e.code == "AGENT_TOOL_FORBIDDEN":
            return json_error(403, e.code, e.message, detail=e.detail)
        if e.code in ("AGENT_MAX_STEPS", "MODEL_NOT_ALLOWED"):
            status = 422 if e.code == "AGENT_MAX_STEPS" else 403
            return json_error(status, e.code, e.message, detail=e.detail)
        return json_error(503, e.code, e.message, detail=e.detail)
    except Exception as e:
        logger.exception("agent_run failed tenant=%s", tenant.tenant_id)
        return json_error(503, "AGENT_RUN_ERROR", str(e))

    response = AgentRunResponse(**result)
    return JSONResponse(status_code=200, content=response.model_dump())
