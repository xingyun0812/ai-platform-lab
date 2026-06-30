"""Plan 执行 HITL gate 与 replan 策略 — #174 PR-6b。

serial / parallel executor 共享；工具级 HITL 仍在 react_loop.execute_tool。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from packages.contracts.agent_schemas import AgentPlan, PlanStep, ToolCallRecord
from packages.observability.context import get_trace_id
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.agent.plan_execution_policy")


def should_gate_plan_approval(*, require_plan_approval: bool, replan_attempt: int) -> bool:
    """Plan 级审批仅在首次执行前触发（replan 重入不再暂停）。"""
    return require_plan_approval and replan_attempt == 0


def gate_plan_approval_or_none(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    model: str | None,
    format_plan_summary: Callable[[Any], str],
) -> dict[str, Any] | None:
    """若需 Plan 审批，写入 store 并返回 pending_plan_approval payload；否则 None。"""
    from packages.agent.plan_approval import store_plan_approval

    plan_approval_id = str(uuid.uuid4())
    store_plan_approval(plan_approval_id, plan, tenant_id, session_id=session_id)
    settings = get_settings()
    return plan_execution_result(
        tenant_id=tenant_id,
        session_id=session_id,
        final_message="",
        tool_calls=[],
        steps=0,
        resolved_model=model or settings.agent_model or settings.default_model,
        status="pending_plan_approval",
        approval_id=None,
        plan=plan,
        plan_steps_completed=0,
        plan_revisions=[],
        plan_approval_id=plan_approval_id,
        plan_summary=format_plan_summary(plan),
    )


def plan_execution_result(
    *,
    tenant_id: str,
    session_id: str,
    final_message: str,
    tool_calls: list[ToolCallRecord],
    steps: int,
    resolved_model: str,
    status: str,
    approval_id: str | None,
    plan: AgentPlan,
    plan_steps_completed: int,
    plan_revisions: list[dict[str, Any]],
    plan_approval_id: str | None = None,
    plan_summary: str | None = None,
) -> dict[str, Any]:
    """serial / parallel executor 统一返回结构。"""
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": final_message,
        "tool_calls": tool_calls,
        "steps": steps,
        "model": resolved_model,
        "trace_id": get_trace_id(),
        "status": status,
        "approval_id": approval_id,
        "plan": plan,
        "plan_steps_completed": plan_steps_completed,
        "plan_revisions": plan_revisions,
    }
    if plan_approval_id is not None:
        payload["plan_approval_id"] = plan_approval_id
    if plan_summary is not None:
        payload["plan_summary"] = plan_summary
    return payload


def append_replan_revision(
    plan_revisions: list[dict[str, Any]],
    *,
    replan_attempt: int,
    failed_step_id: str,
    new_plan: AgentPlan,
) -> None:
    plan_revisions.append(
        {
            "attempt": replan_attempt + 1,
            "failed_step_id": failed_step_id,
            "new_plan_steps_count": len(new_plan.steps),
        }
    )


async def try_replan_and_reexecute(
    *,
    plan: AgentPlan,
    failed_step: PlanStep,
    failure_reason: str,
    model: str | None,
    allowed_models: tuple[str, ...],
    max_replan_attempts: int,
    replan_attempt: int,
    plan_revisions: list[dict[str, Any]],
    reexecute: Callable[[AgentPlan], Awaitable[dict[str, Any]]],
    log_context: str,
) -> dict[str, Any] | None:
    """step/layer 失败后尝试 critic replan；成功则 reexecute 并返回结果，否则 None。"""
    if replan_attempt >= max_replan_attempts:
        return None

    from packages.agent.plan_critic import replan_after_failure

    logger.info(
        "%s: step %s failed, triggering replan attempt %d/%d",
        log_context,
        failed_step.id,
        replan_attempt + 1,
        max_replan_attempts,
    )
    new_plan = await replan_after_failure(
        plan=plan,
        failed_step=failed_step,
        failure_reason=failure_reason,
        model=model,
        allowed_models=allowed_models,
        max_replan_attempts=max_replan_attempts,
        attempt=replan_attempt,
    )
    if new_plan is None:
        logger.warning(
            "%s: critic returned None for step %s, aborting plan",
            log_context,
            failed_step.id,
        )
        return None

    append_replan_revision(
        plan_revisions,
        replan_attempt=replan_attempt,
        failed_step_id=failed_step.id,
        new_plan=new_plan,
    )
    return await reexecute(new_plan)


def merge_tool_calls_from_run_result(
    result: dict[str, Any],
    accumulator: list[ToolCallRecord],
) -> None:
    """从 run_agent 结果合并 tool_calls 到 plan 级 accumulator。"""
    for tc in result.get("tool_calls") or []:
        if isinstance(tc, ToolCallRecord):
            accumulator.append(tc)
        elif isinstance(tc, dict):
            accumulator.append(ToolCallRecord.model_validate(tc))
