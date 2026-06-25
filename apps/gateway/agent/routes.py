from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.quota import get_quota_tracker
from apps.gateway.request_guards import check_rate_limit, check_token_budget
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.multi_agent.blackboard import get_blackboard
from packages.agent.planner import (
    PlannerError,
    execute_plan_with_agent,
    generate_plan,
)
from packages.agent.reasoning import ReasoningModeError, resolve_reasoning_mode
from packages.agent.runner import AgentRunError, run_agent
from packages.agent.session import get_session_store
from packages.contracts.agent_schemas import (
    AgentPlanRequest,
    AgentPlanResponse,
    AgentRunRequest,
    AgentRunResponse,
)
from packages.observability.otel import component_span

logger = logging.getLogger("ai_platform.gateway.agent")

router = APIRouter(prefix="/v1/agent", tags=["agent"])
quota_tracker = get_quota_tracker()


def _resolve_agent_kb_hint(settings, kb_id: str) -> str:
    """Phase F：优先从 prompt registry 取 agent_kb_hint 模板渲染；否则回退硬编码。"""
    if settings.prompt_registry_enabled:
        from packages.prompt import get_registry

        reg = get_registry()
        if reg is not None:
            try:
                entry = reg.get_active("agent_kb_hint")
                if entry is not None and entry.version > 0:
                    return entry.render({"kb_id": kb_id})
            except Exception as e:
                logger.warning("prompt registry agent_kb_hint lookup failed: %s", e)
    return (
        f"默认知识库 kb_id={kb_id}。"
        "调用 get_kb_snippet 时请使用该 kb_id（除非用户指定其他库）。"
    )


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _last_user_goal(messages: list) -> str | None:
    for m in reversed(messages):
        if getattr(m, "role", None) == "user":
            content = getattr(m, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


def _planner_error_response(exc: PlannerError) -> JSONResponse:
    status = 422
    if exc.code == "MODEL_NOT_ALLOWED":
        status = 403
    if exc.code == "PLAN_UPSTREAM_ERROR":
        status = 503
    return json_error(status, exc.code, exc.message, detail=exc.detail)


@router.post("/plan", response_model=AgentPlanResponse)
async def agent_plan(
    body: AgentPlanRequest,
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

    budget_err = check_token_budget(tenant)
    if budget_err is not None:
        return budget_err

    if not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    try:
        with component_span(
            "agent.plan",
            component="agent",
            enabled=settings.otel_enabled,
            tenant_id=tenant.tenant_id,
        ):
            plan, resolved_model = await generate_plan(
                goal=body.goal,
                context=body.context,
                model=body.model,
                allowed_models=tenant.allowed_models,
                allowed_tools=tenant.allowed_tools,
            )
    except PlannerError as e:
        return _planner_error_response(e)

    from packages.observability.context import get_trace_id

    return AgentPlanResponse(
        tenant_id=tenant.tenant_id,
        goal=plan.goal,
        plan=plan,
        model=resolved_model,
        trace_id=get_trace_id(),
    )


@router.post("/run", response_model=AgentRunResponse)
async def agent_run(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_agent_shadow: Annotated[str | None, Header(alias="X-Agent-Shadow")] = None,
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

    budget_err = check_token_budget(tenant)
    if budget_err is not None:
        return budget_err

    if not quota_tracker.has_quota(tenant.tenant_id, tenant.daily_request_quota):
        return json_error(429, "QUOTA_EXCEEDED", "租户日配额已用尽")

    if not body.approval_id and not body.messages and not body.auto_plan:
        return json_error(400, "INVALID_REQUEST", "messages 与 approval_id 不能同时为空")

    if body.auto_plan and not body.approval_id:
        goal = (body.goal or _last_user_goal(body.messages) or "").strip()
        if not goal:
            return json_error(400, "INVALID_REQUEST", "auto_plan 需要 goal 或 user 消息")

    if not body.approval_id and not (settings.llm_api_key or "").strip():
        return json_error(503, "UPSTREAM_NOT_CONFIGURED", "LLM_API_KEY 未配置")

    try:
        resolve_reasoning_mode(body.reasoning_mode, settings.agent_reasoning_mode)
    except ReasoningModeError as e:
        return json_error(400, "INVALID_REQUEST", str(e))

    new_messages: list[dict[str, Any]] = [
        m.model_dump(exclude_none=True) for m in body.messages
    ]
    step_system_messages: list[dict[str, Any]] | None = None
    if body.kb_id:
        hint = _resolve_agent_kb_hint(settings, body.kb_id)
        step_system_messages = [{"role": "system", "content": hint}]
        if not body.auto_plan:
            new_messages = [{"role": "system", "content": hint}, *new_messages]

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
            if body.auto_plan and not body.approval_id:
                goal = (body.goal or _last_user_goal(body.messages) or "").strip()
                plan, _ = await generate_plan(
                    goal=goal,
                    context=None,
                    model=body.model,
                    allowed_models=tenant.allowed_models,
                    allowed_tools=tenant.allowed_tools,
                )
                result = await execute_plan_with_agent(
                    plan=plan,
                    tenant_id=tenant.tenant_id,
                    session_id=body.session_id.strip(),
                    allowed_tools=tenant.allowed_tools,
                    allowed_models=tenant.allowed_models,
                    model=body.model,
                    session_store=get_session_store(),
                    step_system_messages=step_system_messages,
                    require_plan_approval=body.require_plan_approval,
                )
            else:
                result = await run_agent(
                    tenant_id=tenant.tenant_id,
                    session_id=body.session_id.strip(),
                    new_messages=new_messages,
                    allowed_tools=tenant.allowed_tools,
                    allowed_models=tenant.allowed_models,
                    model=body.model,
                    session_store=get_session_store(),
                    token_budget_daily=tenant.token_budget_daily,
                    token_budget_monthly=tenant.token_budget_monthly,
                    shadow_mode=(x_agent_shadow or "").lower() == "true",
                    approval_id=body.approval_id,
                    reasoning_mode=body.reasoning_mode,
                )
    except PlannerError as e:
        return _planner_error_response(e)
    except AgentRunError as e:
        if e.code == "AGENT_PENDING_APPROVAL":
            detail = e.detail or {}
            return JSONResponse(
                status_code=202,
                content={
                    "status": "pending_approval",
                    "approval_id": detail.get("approval_id"),
                    "tool_name": detail.get("tool_name"),
                    "arguments": detail.get("arguments"),
                    "tenant_id": tenant.tenant_id,
                    "session_id": body.session_id.strip(),
                    "final_message": "",
                    "tool_calls": [],
                    "steps": 0,
                    "model": body.model or settings.default_model or settings.agent_model,
                    "trace_id": None,
                },
            )
        if e.code == "AGENT_APPROVAL_INVALID":
            return json_error(422, e.code, e.message, detail=e.detail)
        if e.code == "AGENT_TOOL_FORBIDDEN":
            return json_error(403, e.code, e.message, detail=e.detail)
        if e.code == "AGENT_INVALID_REASONING_MODE":
            return json_error(400, e.code, e.message, detail=e.detail)
        if e.code in ("AGENT_MAX_STEPS", "MODEL_NOT_ALLOWED"):
            status = 422 if e.code == "AGENT_MAX_STEPS" else 403
            return json_error(status, e.code, e.message, detail=e.detail)
        return json_error(503, e.code, e.message, detail=e.detail)
    except Exception as e:
        logger.exception("agent_run failed tenant=%s", tenant.tenant_id)
        return json_error(503, "AGENT_RUN_ERROR", str(e))

    platform = result.pop("_platform", None)
    response = AgentRunResponse(**result)
    content = response.model_dump()
    if platform:
        content["_platform"] = platform
    status_code = 202 if content.get("status") in ("pending_approval", "pending_plan_approval") else 200
    return JSONResponse(status_code=status_code, content=content)


@router.get("/blackboard/{session_id}")
async def get_agent_blackboard(
    session_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
    limit: int = 100,
) -> JSONResponse:
    """Multi-Agent 共享黑板 — Phase O #89。"""
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant
    if not session_id.strip():
        return json_error(400, "INVALID_SESSION", "session_id 不能为空")
    bb = get_blackboard()
    entries = bb.list_entries(tenant.tenant_id, session_id.strip(), limit=max(1, min(limit, 500)))
    return JSONResponse(
        {
            "tenant_id": tenant.tenant_id,
            "session_id": session_id.strip(),
            "entries": [e.to_dict() for e in entries],
            "count": len(entries),
        }
    )
