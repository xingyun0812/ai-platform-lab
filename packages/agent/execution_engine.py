"""统一 Plan 执行 facade — planner 轨 / orchestrator 轨分派（#162）。"""

from __future__ import annotations

import logging
from typing import Any

from packages.agent.planner import get_plan_executor
from packages.contracts.agent_schemas import AgentPlan, ToolCallRecord
from packages.observability.context import get_trace_id
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.agent.execution_engine")

_PLANNER_BACKEND = "planner"
_ORCHESTRATOR_BACKEND = "orchestrator"


def resolve_plan_execution_backend() -> str:
    """读取 ``plan_execution_backend``，默认 ``planner``（向后兼容）。"""
    raw = getattr(get_settings(), "plan_execution_backend", _PLANNER_BACKEND)
    backend = str(raw or _PLANNER_BACKEND).strip().lower()
    if backend not in {_PLANNER_BACKEND, _ORCHESTRATOR_BACKEND}:
        logger.warning("unknown plan_execution_backend=%r, fallback to planner", raw)
        return _PLANNER_BACKEND
    return backend


async def execute_plan(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: Any,
    step_system_messages: list[dict[str, Any]] | None = None,
    max_replan_attempts: int | None = None,
    require_plan_approval: bool = False,
    long_run_task_id: str | None = None,
    plan_execution_mode: str | None = None,
) -> dict[str, Any]:
    """执行 AgentPlan，按 settings 分派到 planner 或 orchestrator 轨。"""
    settings = get_settings()
    replan_attempts = (
        max_replan_attempts
        if max_replan_attempts is not None
        else settings.plan_max_replan_attempts
    )
    mode = plan_execution_mode or settings.plan_execution_mode
    backend = resolve_plan_execution_backend()

    if backend == _ORCHESTRATOR_BACKEND:
        return await _execute_plan_via_orchestrator(
            plan=plan,
            tenant_id=tenant_id,
            session_id=session_id,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
            model=model,
            session_store=session_store,
            step_system_messages=step_system_messages,
            max_replan_attempts=replan_attempts,
            require_plan_approval=require_plan_approval,
            long_run_task_id=long_run_task_id,
            plan_execution_mode=mode,
        )

    executor = get_plan_executor(mode=mode)
    return await executor(
        plan=plan,
        tenant_id=tenant_id,
        session_id=session_id,
        allowed_tools=allowed_tools,
        allowed_models=allowed_models,
        model=model,
        session_store=session_store,
        step_system_messages=step_system_messages,
        max_replan_attempts=replan_attempts,
        require_plan_approval=require_plan_approval,
        long_run_task_id=long_run_task_id,
    )


async def _execute_plan_via_orchestrator(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: Any,
    step_system_messages: list[dict[str, Any]] | None,
    max_replan_attempts: int,
    require_plan_approval: bool,
    long_run_task_id: str | None,
    plan_execution_mode: str,
) -> dict[str, Any]:
    """Orchestrator 轨：plan_to_orchestrator_workflow + execute_workflow。"""
    # Plan 审批、长程任务、重规划仍走成熟 planner 路径（PR-3/4 再收敛）
    if require_plan_approval or long_run_task_id:
        executor = get_plan_executor(mode=plan_execution_mode)
        return await executor(
            plan=plan,
            tenant_id=tenant_id,
            session_id=session_id,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
            model=model,
            session_store=session_store,
            step_system_messages=step_system_messages,
            max_replan_attempts=max_replan_attempts,
            require_plan_approval=require_plan_approval,
            long_run_task_id=long_run_task_id,
        )

    from packages.agent.orchestrator.engine import execute_workflow
    from packages.agent.plan_workflow import plan_to_orchestrator_workflow

    workflow_id = f"plan-{tenant_id}-{session_id}"[:120]
    try:
        workflow = plan_to_orchestrator_workflow(plan, workflow_id=workflow_id)
    except ValueError as exc:
        return _failed_plan_result(
            plan=plan,
            tenant_id=tenant_id,
            session_id=session_id,
            model=model,
            message=str(exc),
            plan_steps_completed=0,
        )

    wf_result = await execute_workflow(
        workflow,
        inputs={
            "tenant_id": tenant_id,
            "session_id": session_id,
            "allowed_tools": allowed_tools,
            "allowed_models": allowed_models,
            "model": model,
            "session_store": session_store,
            "step_system_messages": step_system_messages,
        },
    )

    return _map_workflow_result_to_plan_dict(
        plan=plan,
        tenant_id=tenant_id,
        session_id=session_id,
        model=model,
        wf_result=wf_result,
    )


def _normalize_tool_calls(raw: Any) -> list[ToolCallRecord]:
    out: list[ToolCallRecord] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, ToolCallRecord):
            out.append(item)
        elif isinstance(item, dict):
            out.append(ToolCallRecord.model_validate(item))
    return out


def _map_workflow_result_to_plan_dict(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    model: str | None,
    wf_result: Any,
) -> dict[str, Any]:
    all_tool_calls: list[ToolCallRecord] = []
    agent_steps = 0
    final_message = ""
    resolved_model = model or ""
    last_status = "completed"
    last_approval_id: str | None = None
    completed = 0

    for step in plan.steps:
        step_out = wf_result.outputs.get(step.id)
        if not isinstance(step_out, dict):
            break
        completed += 1
        status = str(step_out.get("status") or "completed")
        last_status = status
        final_message = str(step_out.get("final_message") or final_message)
        resolved_model = str(step_out.get("model") or resolved_model)
        last_approval_id = step_out.get("approval_id") or last_approval_id
        all_tool_calls.extend(_normalize_tool_calls(step_out.get("tool_calls")))
        if status == "pending_approval":
            break
        if status == "failed":
            break

    if wf_result.status == "failed" and last_status == "completed":
        last_status = "failed"
        final_message = str(wf_result.error or final_message or "workflow failed")

    base: dict[str, Any] = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": final_message,
        "tool_calls": all_tool_calls,
        "steps": agent_steps,
        "model": resolved_model,
        "trace_id": get_trace_id(),
        "status": last_status,
        "approval_id": last_approval_id,
        "plan": plan,
        "plan_steps_completed": completed if last_status != "completed" else len(plan.steps),
        "plan_revisions": [],
        "execution_backend": _ORCHESTRATOR_BACKEND,
        "workflow_id": wf_result.workflow_id,
        "workflow_trace": wf_result.trace,
    }
    if last_status == "completed" and wf_result.status == "completed":
        base["plan_steps_completed"] = len(plan.steps)
    return base


def _failed_plan_result(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    model: str | None,
    message: str,
    plan_steps_completed: int,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": message,
        "tool_calls": [],
        "steps": 0,
        "model": model or "",
        "trace_id": get_trace_id(),
        "status": "failed",
        "approval_id": None,
        "plan": plan,
        "plan_steps_completed": plan_steps_completed,
        "plan_revisions": [],
        "execution_backend": _ORCHESTRATOR_BACKEND,
    }
