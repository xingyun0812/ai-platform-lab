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
from packages.agent.graph_runtime import GraphRuntimeError, execute_agent_graph
from packages.agent.multi_agent.blackboard import get_blackboard
from packages.agent.planner import (
    PlannerError,
    generate_plan,
)
from packages.agent.reasoning import ReasoningModeError, resolve_reasoning_mode
from packages.agent.runner import AgentRunError
from packages.agent.session import get_session_store
from packages.contracts.agent_schemas import (
    AgentPlanRequest,
    AgentPlanResponse,
    AgentRunRequest,
    AgentRunResponse,
)
from packages.contracts.agent_schemas import (
    DebateResult as DebateResultSchema,
)
from packages.contracts.agent_schemas import (
    TotResult as TotResultSchema,
)
from packages.contracts.agent_schemas import (
    ResearchResult as ResearchResultSchema,
)
from packages.contracts.agent_schemas import (
    SelfRefineResult as SelfRefineResultSchema,
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
                tenant_id=tenant.tenant_id,
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

    if (
        not body.approval_id
        and not body.plan_approval_id
        and not body.messages
        and not body.auto_plan
    ):
        return json_error(400, "INVALID_REQUEST", "messages / approval_id / plan_approval_id / auto_plan 至少一项")

    if body.auto_plan and not body.approval_id and not body.plan_approval_id:
        goal = (body.goal or _last_user_goal(body.messages) or "").strip()
        if not goal:
            return json_error(400, "INVALID_REQUEST", "auto_plan 需要 goal 或 user 消息")

    if (
        not body.approval_id
        and not body.plan_approval_id
        and not (settings.llm_api_key or "").strip()
    ):
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
            result = await execute_agent_graph(
                body=body,
                tenant=tenant,
                session_store=get_session_store(),
                new_messages=new_messages,
                step_system_messages=step_system_messages,
                shadow_mode=(x_agent_shadow or "").lower() == "true",
            )
    except GraphRuntimeError as e:
        status = 404 if e.code.endswith("NOT_FOUND") else 422
        if e.code == "PLAN_APPROVAL_PENDING":
            status = 409
        return json_error(status, e.code, e.message, detail=e.detail)
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
    graph_state = result.pop("_graph_state", None)
    resumed_from = result.pop("resumed_from_plan_approval_id", None)
    response = AgentRunResponse(**result)
    content = response.model_dump()
    if platform:
        content["_platform"] = platform
    if graph_state:
        content["_graph_state"] = graph_state
    if resumed_from:
        content["resumed_from_plan_approval_id"] = resumed_from
    status_code = 202 if content.get("status") in ("pending_approval", "pending_plan_approval") else 200
    return JSONResponse(status_code=status_code, content=content)


@router.post("/tot")
async def agent_tot(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase S: Tree of Thoughts 推理。

    独立 ToT 推理端点，不经过 ReAct 循环。
    调用 ToT 树搜索后返回最优答案 + 搜索树结构。
    """
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

    goal = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not goal:
        return json_error(400, "INVALID_REQUEST", "ToT 需要 goal 或 user 消息")

    tot_cfg = body.tot_config
    from packages.agent.tot import TotConfig as TotConfigDC
    from packages.agent.tot import run_tot

    cfg = TotConfigDC(
        search_algorithm=tot_cfg.search_algorithm if tot_cfg else "bfs",
        branching_factor=tot_cfg.branching_factor if tot_cfg else 3,
        beam_width=tot_cfg.beam_width if tot_cfg else 2,
        max_depth=tot_cfg.max_depth if tot_cfg else 5,
        max_total_nodes=tot_cfg.max_total_nodes if tot_cfg else 50,
        value_threshold=tot_cfg.value_threshold if tot_cfg else None,
        temperature=tot_cfg.temperature if tot_cfg else 0.7,
        timeout_seconds=tot_cfg.timeout_seconds if tot_cfg else 120.0,
    )

    try:
        result = await run_tot(
            goal=goal,
            config=cfg,
            model=body.model,
        )
    except Exception as e:
        logger.exception("agent_tot failed tenant=%s", tenant.tenant_id)
        return json_error(503, "TOT_ERROR", str(e))

    tree_dict = result.tree.to_dict() if result.tree else None
    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.best_answer or "",
        "tot_result": TotResultSchema(
            best_answer=result.best_answer,
            best_value=result.best_value,
            total_nodes=result.total_nodes,
            search_depth=result.search_depth,
            execution_time_ms=result.execution_time_ms,
            tree=tree_dict,
            error=result.error,
        ).model_dump(exclude_none=True),
    })


@router.post("/debate")
async def agent_debate(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase T: Multi-Agent Debate 推理。

    多个 Agent 围绕同一问题独立推理、互相评议、收敛答案。
    """
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

    question = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not question:
        return json_error(400, "INVALID_REQUEST", "Debate 需要 goal 或 user 消息")

    dc = body.debate_config
    from packages.agent.debate import DebateConfig as DebateConfigDC
    from packages.agent.debate import run_debate

    cfg = DebateConfigDC(
        num_proposers=dc.num_proposers if dc else 3,
        num_rounds=dc.num_rounds if dc else 2,
        temperature=dc.temperature if dc else 0.7,
        critic_temperature=dc.critic_temperature if dc else 0.3,
        judge_temperature=dc.judge_temperature if dc else 0.1,
        timeout_seconds=dc.timeout_seconds if dc else 120.0,
    )

    try:
        result = await run_debate(
            question=question,
            config=cfg,
            model=body.model,
            tenant_id=tenant.tenant_id,
            session_id=body.session_id.strip(),
            allowed_tools=tenant.allowed_tools,
            allowed_models=tenant.allowed_models,
        )
    except Exception as e:
        logger.exception("agent_debate failed tenant=%s", tenant.tenant_id)
        return json_error(503, "DEBATE_ERROR", str(e))

    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.verdict or "",
        "debate_result": DebateResultSchema(
            question=result.question,
            verdict=result.verdict,
            verdict_confidence=result.verdict_confidence,
            verdict_agent=result.verdict_agent,
            proposals=[],
            critiques=[],
            num_rounds_completed=result.num_rounds_completed,
            execution_time_ms=result.execution_time_ms,
            error=result.error,
        ).model_dump(exclude_none=True),
    })


@router.post("/research")
async def agent_research(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase U: Deep Research 推理。"""
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
    question = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not question:
        return json_error(400, "INVALID_REQUEST", "Research 需要 goal 或 user 消息")
    rc = body.research_config
    from packages.agent.research import ResearchConfig as ResearchConfigDC, run_research
    cfg = ResearchConfigDC(
        max_sub_questions=rc.max_sub_questions if rc else 5,
        results_per_query=rc.results_per_query if rc else 5,
        max_depth=rc.max_depth if rc else 2,
        timeout_seconds=rc.timeout_seconds if rc else 300.0,
        temperature=rc.temperature if rc else 0.3,
    )
    try:
        result = await run_research(question=question, config=cfg, model=body.model)
    except Exception as e:
        logger.exception("agent_research failed tenant=%s", tenant.tenant_id)
        return json_error(503, "RESEARCH_ERROR", str(e))
    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.report[:500] if result.report else "",
        "research_result": ResearchResultSchema(
            question=result.question,
            report=result.report,
            sub_questions=result.sub_questions,
            num_sources_consulted=result.num_sources_consulted,
            depth_completed=result.depth_completed,
            execution_time_ms=result.execution_time_ms,
            error=result.error,
        ).model_dump(exclude_none=True),
    })


@router.post("/self-refine")
async def agent_self_refine(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase W: Self-Refine 推理。

    独立 Self-Refine 端点：首轮生成 → 自我反馈 → 自我修正 → 迭代收敛。
    不经过 ReAct 循环。
    """
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

    prompt = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not prompt:
        return json_error(400, "INVALID_REQUEST", "Self-Refine 需要 goal 或 user 消息")

    src = body.self_refine_config
    from packages.agent.self_refine import SelfRefineConfig as SelfRefineConfigDC, run_self_refine

    cfg = SelfRefineConfigDC(
        max_iterations=src.max_iterations if src else 5,
        generator_model=src.generator_model if src else None,
        feedback_model=src.feedback_model if src else None,
        convergence_strategy=src.convergence_strategy if src else "hybrid",
        convergence_threshold=src.convergence_threshold if src else 0.85,
        max_total_llm_calls=src.max_total_llm_calls if src else 15,
        temperature=src.temperature if src else 0.3,
        timeout_seconds=src.timeout_seconds if src else 120.0,
    )

    try:
        with component_span("self_refine", {"tenant": tenant.tenant_id}):
            result = await run_self_refine(
                prompt=prompt,
                config=cfg,
                model=body.model,
            )
    except Exception as e:
        logger.exception("agent_self_refine failed tenant=%s", tenant.tenant_id)
        return json_error(503, "SELF_REFINE_ERROR", str(e))

    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.final_output,
        "self_refine_result": SelfRefineResultSchema(
            prompt=result.prompt,
            final_output=result.final_output,
            iterations_completed=result.iterations_completed,
            converged=result.converged,
            convergence_reason=result.convergence_reason,
            trace=[t.to_dict() for t in result.trace],
            execution_time_ms=result.execution_time_ms,
            total_llm_calls=result.total_llm_calls,
            error=result.error,
            success=result.success,
        ).model_dump(exclude_none=True),
    })


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
