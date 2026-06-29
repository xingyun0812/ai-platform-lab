"""统一图执行入口 — 最小 LangGraph 等价物 facade。"""

from __future__ import annotations

import logging
from typing import Any

from apps.gateway.settings import Settings
from apps.gateway.tenants import TenantRecord
from packages.agent.graph_state import AgentGraphState
from packages.agent.plan_approval import get_plan_approval
from packages.agent.planner import (
    PlannerError,
    generate_plan,
    get_plan_executor,
)
from packages.agent.runner import run_agent
from packages.agent.run_lifecycle import finalize_agent_run_result

logger = logging.getLogger("ai_platform.agent.graph_runtime")


class GraphRuntimeError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


def _last_user_goal(messages: list) -> str:
    for msg in reversed(messages):
        if getattr(msg, "role", None) == "user" and getattr(msg, "content", None):
            return str(msg.content).strip()
    return ""


async def execute_agent_graph(
    *,
    body: Any,
    tenant: TenantRecord,
    settings: Settings,
    session_store: Any,
    new_messages: list[dict[str, Any]],
    step_system_messages: list[dict[str, Any]] | None,
    shadow_mode: bool,
) -> dict[str, Any]:
    """统一 Agent 图执行：plan_approval resume / tool approval resume / auto_plan / react。"""
    session_id = body.session_id.strip()

    if body.plan_approval_id:
        return await _resume_approved_plan(
            plan_approval_id=body.plan_approval_id,
            tenant=tenant,
            session_id=session_id,
            settings=settings,
            session_store=session_store,
            model=body.model,
            step_system_messages=step_system_messages,
        )

    if body.approval_id:
        result = await run_agent(
            tenant_id=tenant.tenant_id,
            session_id=session_id,
            new_messages=new_messages,
            allowed_tools=tenant.allowed_tools,
            allowed_models=tenant.allowed_models,
            model=body.model,
            session_store=session_store,
            token_budget_daily=tenant.token_budget_daily,
            token_budget_monthly=tenant.token_budget_monthly,
            shadow_mode=shadow_mode,
            approval_id=body.approval_id,
            reasoning_mode=body.reasoning_mode,
        )
        result["_graph_state"] = AgentGraphState(
            tenant_id=tenant.tenant_id,
            session_id=session_id,
            mode="react",
            status="paused" if result.get("status") == "pending_approval" else "completed",
            interrupt_reason="tool_approval" if result.get("approval_id") else None,
            interrupt_id=result.get("approval_id"),
        ).to_dict()
        return finalize_agent_run_result(
            result,
            tenant_id=tenant.tenant_id,
            model=body.model,
        )

    if body.auto_plan:
        goal = (body.goal or _last_user_goal(body.messages) or "").strip()
        if not goal:
            raise GraphRuntimeError("INVALID_REQUEST", "auto_plan 需要 goal 或 user 消息")
        plan, _ = await generate_plan(
            goal=goal,
            context=None,
            model=body.model,
            allowed_models=tenant.allowed_models,
            allowed_tools=tenant.allowed_tools,
            tenant_id=tenant.tenant_id,
        )
        require_approval = body.require_plan_approval or settings.plan_require_approval
        execute_plan = get_plan_executor(mode=settings.plan_execution_mode)
        result = await execute_plan(
            plan=plan,
            tenant_id=tenant.tenant_id,
            session_id=session_id,
            allowed_tools=tenant.allowed_tools,
            allowed_models=tenant.allowed_models,
            model=body.model,
            session_store=session_store,
            step_system_messages=step_system_messages,
            max_replan_attempts=settings.plan_max_replan_attempts,
            require_plan_approval=require_approval,
        )
        result["_graph_state"] = AgentGraphState(
            tenant_id=tenant.tenant_id,
            session_id=session_id,
            mode="plan",
            status="paused" if result.get("status") == "pending_plan_approval" else "completed",
            plan=plan,
            interrupt_reason="plan_approval" if result.get("plan_approval_id") else None,
            interrupt_id=result.get("plan_approval_id"),
        ).to_dict()
        return finalize_agent_run_result(
            result,
            tenant_id=tenant.tenant_id,
            model=body.model,
            plan=plan,
        )

    result = await run_agent(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        new_messages=new_messages,
        allowed_tools=tenant.allowed_tools,
        allowed_models=tenant.allowed_models,
        model=body.model,
        session_store=session_store,
        token_budget_daily=tenant.token_budget_daily,
        token_budget_monthly=tenant.token_budget_monthly,
        shadow_mode=shadow_mode,
        approval_id=None,
        reasoning_mode=body.reasoning_mode,
    )
    result["_graph_state"] = AgentGraphState(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        mode="react",
        status="paused" if result.get("status") == "pending_approval" else "completed",
        interrupt_reason="tool_approval" if result.get("approval_id") else None,
        interrupt_id=result.get("approval_id"),
    ).to_dict()
    return finalize_agent_run_result(
        result,
        tenant_id=tenant.tenant_id,
        model=body.model,
    )


async def _resume_approved_plan(
    *,
    plan_approval_id: str,
    tenant: TenantRecord,
    session_id: str,
    settings: Settings,
    session_store: Any,
    model: str | None,
    step_system_messages: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    entry = get_plan_approval(plan_approval_id)
    if entry is None:
        raise GraphRuntimeError(
            "PLAN_APPROVAL_NOT_FOUND",
            f"plan_approval_id 不存在: {plan_approval_id}",
        )
    if entry.tenant_id != tenant.tenant_id:
        raise GraphRuntimeError("TENANT_MISMATCH", "plan_approval 租户不匹配")
    if entry.status == "rejected":
        raise GraphRuntimeError("PLAN_APPROVAL_REJECTED", "该 Plan 已被拒绝")
    if entry.status != "approved":
        raise GraphRuntimeError(
            "PLAN_APPROVAL_PENDING",
            "Plan 尚未审批通过",
            detail={"status": entry.status},
        )
    if entry.session_id and entry.session_id != session_id:
        logger.warning(
            "plan resume session mismatch: approval=%s request=%s",
            entry.session_id,
            session_id,
        )

    execute_plan = get_plan_executor(mode=settings.plan_execution_mode)
    try:
        result = await execute_plan(
            plan=entry.plan,
            tenant_id=tenant.tenant_id,
            session_id=session_id or entry.session_id or session_id,
            allowed_tools=tenant.allowed_tools,
            allowed_models=tenant.allowed_models,
            model=model,
            session_store=session_store,
            step_system_messages=step_system_messages,
            max_replan_attempts=settings.plan_max_replan_attempts,
            require_plan_approval=False,
        )
    except PlannerError as e:
        raise GraphRuntimeError(e.code, e.message, e.detail) from e

    result["_graph_state"] = AgentGraphState(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        mode="plan",
        status="completed" if result.get("status") == "completed" else str(result.get("status", "running")),
        plan=entry.plan,
        interrupt_id=plan_approval_id,
    ).to_dict()
    result["resumed_from_plan_approval_id"] = plan_approval_id
    return finalize_agent_run_result(
        result,
        tenant_id=tenant.tenant_id,
        model=model,
        plan=entry.plan,
    )