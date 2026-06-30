"""Plan 执行器 — serial / parallel 共享 PlanExecutionContext（#176 PR-6c）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from packages.agent.perf_metrics import get_agent_perf_metrics
from packages.agent.plan_execution_policy import (
    gate_plan_approval_or_none,
    merge_tool_calls_from_run_result,
    plan_execution_result,
    should_gate_plan_approval,
    try_replan_and_reexecute,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep, ToolCallRecord
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.agent.plan_executor")


@dataclass
class PlanExecutionContext:
    """Plan 执行共享上下文 — serial / parallel 共用 accumulators 与 step 运行。"""

    plan: AgentPlan
    tenant_id: str
    session_id: str
    allowed_tools: tuple[str, ...]
    allowed_models: tuple[str, ...]
    model: str | None
    session_store: Any
    step_system_messages: list[dict[str, Any]] | None
    runner: Callable[..., Awaitable[dict[str, Any]]]
    max_replan_attempts: int
    replan_attempt: int
    plan_revisions: list[dict[str, Any]]
    require_plan_approval: bool
    long_run_task_id: str | None = None
    all_tool_calls: list[ToolCallRecord] = field(default_factory=list)
    agent_steps: int = 0
    final_message: str = ""
    resolved_model: str = ""
    last_status: str = "completed"
    last_approval_id: str | None = None
    completed_count: int = 0
    completed_step_ids: set[str] = field(default_factory=set)

    @classmethod
    async def create(
        cls,
        *,
        plan: AgentPlan,
        tenant_id: str,
        session_id: str,
        allowed_tools: tuple[str, ...],
        allowed_models: tuple[str, ...],
        model: str | None,
        session_store: Any,
        step_system_messages: list[dict[str, Any]] | None,
        runner: Callable[..., Awaitable[dict[str, Any]]],
        max_replan_attempts: int,
        replan_attempt: int,
        plan_revisions: list[dict[str, Any]] | None,
        require_plan_approval: bool,
        long_run_task_id: str | None = None,
    ) -> tuple[PlanExecutionContext | None, dict[str, Any] | None]:
        """构建上下文；若 Plan 审批 gate 触发则返回 (None, early_response)。"""
        if should_gate_plan_approval(
            require_plan_approval=require_plan_approval,
            replan_attempt=replan_attempt,
        ):
            from packages.agent.planner import format_plan_summary

            gated = gate_plan_approval_or_none(
                plan=plan,
                tenant_id=tenant_id,
                session_id=session_id,
                model=model,
                format_plan_summary=format_plan_summary,
            )
            return None, gated

        settings = get_settings()
        ctx = cls(
            plan=plan,
            tenant_id=tenant_id,
            session_id=session_id,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
            model=model,
            session_store=session_store,
            step_system_messages=step_system_messages,
            runner=runner,
            max_replan_attempts=max_replan_attempts,
            replan_attempt=replan_attempt,
            plan_revisions=list(plan_revisions or []),
            require_plan_approval=require_plan_approval,
            long_run_task_id=long_run_task_id,
            resolved_model=model or settings.agent_model or settings.default_model,
        )
        if long_run_task_id:
            from packages.agent.long_horizon import get_long_run

            lr_task = await get_long_run(long_run_task_id)
            if lr_task is not None:
                ctx.completed_step_ids = {
                    s.step_id for s in lr_task.step_states if s.status == "completed"
                }
        return ctx, None

    def result(
        self,
        *,
        plan_steps_completed: int,
        status: str | None = None,
    ) -> dict[str, Any]:
        return plan_execution_result(
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            final_message=self.final_message,
            tool_calls=self.all_tool_calls,
            steps=self.agent_steps,
            resolved_model=self.resolved_model,
            status=status or self.last_status,
            approval_id=self.last_approval_id,
            plan=self.plan,
            plan_steps_completed=plan_steps_completed,
            plan_revisions=self.plan_revisions,
        )

    def absorb_step_result(self, result: dict[str, Any]) -> str:
        """合并单 step run_agent 结果，返回 step_status。"""
        self.resolved_model = result.get("model") or self.resolved_model
        self.agent_steps += int(result.get("steps") or 0)
        self.final_message = str(result.get("final_message") or self.final_message)
        step_status = str(result.get("status") or "completed")
        self.last_status = step_status
        self.last_approval_id = result.get("approval_id") or self.last_approval_id
        merge_tool_calls_from_run_result(result, self.all_tool_calls)
        return step_status

    async def run_plan_step(
        self,
        step: PlanStep,
        *,
        step_index: int,
        total_steps: int,
        run_session_id: str | None = None,
        prepend_system: bool = False,
        log_prefix: str = "plan_executor",
    ) -> dict[str, Any]:
        from packages.agent.planner import format_step_user_message

        step_msg = format_step_user_message(step, index=step_index, total=total_steps)
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": step_msg}]
        if prepend_system and self.step_system_messages:
            new_messages = [*self.step_system_messages, *new_messages]
        pinned = (step.tool_hint,) if getattr(step, "tool_hint", None) else None
        try:
            return await self.runner(
                tenant_id=self.tenant_id,
                session_id=run_session_id or self.session_id,
                new_messages=new_messages,
                allowed_tools=self.allowed_tools,
                allowed_models=self.allowed_models,
                model=self.model,
                session_store=self.session_store,
                pinned_tools=pinned,
            )
        except Exception as exc:
            logger.warning("%s: step %s raised exception: %s", log_prefix, step.id, exc)
            return {
                "final_message": str(exc),
                "tool_calls": [],
                "steps": 0,
                "model": self.resolved_model,
                "status": "failed",
            }

    async def try_replan_after_failure(
        self,
        *,
        failed_step: PlanStep,
        failure_reason: str,
        reexecute: Callable[[AgentPlan], Awaitable[dict[str, Any]]],
        log_context: str,
    ) -> dict[str, Any] | None:
        return await try_replan_and_reexecute(
            plan=self.plan,
            failed_step=failed_step,
            failure_reason=failure_reason,
            model=self.model,
            allowed_models=self.allowed_models,
            max_replan_attempts=self.max_replan_attempts,
            replan_attempt=self.replan_attempt,
            plan_revisions=self.plan_revisions,
            reexecute=reexecute,
            log_context=log_context,
        )


async def execute_plan_serial(ctx: PlanExecutionContext) -> dict[str, Any]:
    from packages.agent.planner import ordered_plan_steps

    steps = ordered_plan_steps(ctx.plan)
    total = len(steps)

    for idx, step in enumerate(steps, start=1):
        result = await ctx.run_plan_step(
            step,
            step_index=idx,
            total_steps=total,
            prepend_system=idx == 1,
            log_prefix="execute_plan_with_agent",
        )
        step_status = ctx.absorb_step_result(result)

        if step_status == "pending_approval":
            get_agent_perf_metrics().record_plan_steps(tenant_id=ctx.tenant_id, steps=idx - 1)
            return ctx.result(plan_steps_completed=idx - 1)

        if step_status == "failed":
            failure_reason = ctx.final_message or f"step {step.id} returned status=failed"

            async def _reexecute(new_plan: AgentPlan) -> dict[str, Any]:
                from packages.agent.planner import execute_plan_with_agent

                return await execute_plan_with_agent(
                    plan=new_plan,
                    tenant_id=ctx.tenant_id,
                    session_id=ctx.session_id,
                    allowed_tools=ctx.allowed_tools,
                    allowed_models=ctx.allowed_models,
                    model=ctx.model,
                    session_store=ctx.session_store,
                    step_system_messages=ctx.step_system_messages,
                    run_agent_fn=ctx.runner,
                    max_replan_attempts=ctx.max_replan_attempts,
                    _replan_attempt=ctx.replan_attempt + 1,
                    _plan_revisions=ctx.plan_revisions,
                )

            replanned = await ctx.try_replan_after_failure(
                failed_step=step,
                failure_reason=failure_reason,
                reexecute=_reexecute,
                log_context="execute_plan_with_agent",
            )
            if replanned is not None:
                return replanned

            get_agent_perf_metrics().record_plan_steps(tenant_id=ctx.tenant_id, steps=idx)
            return ctx.result(plan_steps_completed=idx, status="failed")

    get_agent_perf_metrics().record_plan_steps(tenant_id=ctx.tenant_id, steps=total)
    return ctx.result(plan_steps_completed=total)


async def execute_plan_parallel(ctx: PlanExecutionContext) -> dict[str, Any]:
    from packages.agent.planner import plan_execution_layers

    layers = plan_execution_layers(ctx.plan.steps)
    total_steps = len(ctx.plan.steps)

    for layer_idx, layer in enumerate(layers):
        pending_steps = [s for s in layer if s.id not in ctx.completed_step_ids]
        skipped_count = len(layer) - len(pending_steps)
        ctx.completed_count += skipped_count

        async def _run_step(step: PlanStep, layer_pos: int) -> dict[str, Any]:
            return await ctx.run_plan_step(
                step,
                step_index=layer_pos + 1,
                total_steps=total_steps,
                run_session_id=f"{ctx.session_id}__step_{step.id}",
                prepend_system=layer_idx == 0 and layer_pos == 0,
                log_prefix="execute_plan_parallel",
            )

        raw_results = await asyncio.gather(
            *(_run_step(step, i) for i, step in enumerate(pending_steps)),
            return_exceptions=True,
        )

        layer_has_pending = False
        layer_completed = True
        layer_outcomes: list[Any] = []

        for step, raw in zip(pending_steps, raw_results, strict=True):
            sub_session_id = f"{ctx.session_id}__step_{step.id}"
            if isinstance(raw, BaseException):
                logger.warning(
                    "parallel step %s failed with exception: %s", step.id, raw, exc_info=False
                )
                ctx.last_status = "failed"
                layer_completed = False
                ctx.completed_count += 1
                if ctx.long_run_task_id:
                    from packages.agent.long_horizon import LayerStepOutcome

                    layer_outcomes.append(
                        LayerStepOutcome(
                            step_id=step.id,
                            status="failed",
                            error=str(raw),
                            sub_session_id=sub_session_id,
                        )
                    )
                continue

            tc_summary: list[dict[str, Any]] = []
            for tc in raw.get("tool_calls") or []:
                if isinstance(tc, ToolCallRecord):
                    tc_summary.append({"tool": tc.tool_name, "status": tc.status})
                elif isinstance(tc, dict):
                    tc_summary.append(
                        {
                            "tool": tc.get("tool_name") or tc.get("tool"),
                            "status": tc.get("status"),
                        }
                    )

            step_status = ctx.absorb_step_result(raw)
            stored_status = "running" if step_status == "pending_approval" else step_status

            if ctx.long_run_task_id:
                from packages.agent.long_horizon import LayerStepOutcome

                layer_outcomes.append(
                    LayerStepOutcome(
                        step_id=step.id,
                        status=stored_status,
                        error=ctx.final_message if step_status == "failed" else None,
                        sub_session_id=sub_session_id,
                        tool_calls_summary=tc_summary,
                    )
                )

            if step_status == "pending_approval":
                layer_has_pending = True
                layer_completed = False
                ctx.last_status = "pending_approval"
            elif step_status == "failed":
                ctx.last_status = "failed"
                layer_completed = False
            ctx.completed_count += 1

        if ctx.long_run_task_id and layer_outcomes:
            from packages.agent.long_horizon import record_layer_step_outcomes

            await record_layer_step_outcomes(ctx.long_run_task_id, outcomes=layer_outcomes)

        get_agent_perf_metrics().record_parallel_steps(tenant_id=ctx.tenant_id, steps=len(layer))

        if ctx.long_run_task_id and layer_completed and ctx.last_status != "failed":
            from packages.agent.long_horizon import checkpoint_task

            try:
                await checkpoint_task(ctx.long_run_task_id)
            except Exception as exc:
                logger.warning("auto-checkpoint failed: %s", exc)

        if layer_has_pending:
            return ctx.result(plan_steps_completed=ctx.completed_count)

        if ctx.last_status == "failed":
            failed_step = layer[0]
            failure_reason = ctx.final_message or f"layer {layer_idx} had failed steps"

            async def _reexecute(new_plan: AgentPlan) -> dict[str, Any]:
                from packages.agent.planner import execute_plan_parallel

                return await execute_plan_parallel(
                    plan=new_plan,
                    tenant_id=ctx.tenant_id,
                    session_id=ctx.session_id,
                    allowed_tools=ctx.allowed_tools,
                    allowed_models=ctx.allowed_models,
                    model=ctx.model,
                    session_store=ctx.session_store,
                    step_system_messages=ctx.step_system_messages,
                    run_agent_fn=ctx.runner,
                    max_replan_attempts=ctx.max_replan_attempts,
                    _replan_attempt=ctx.replan_attempt + 1,
                    _plan_revisions=ctx.plan_revisions,
                    long_run_task_id=ctx.long_run_task_id,
                )

            replanned = await ctx.try_replan_after_failure(
                failed_step=failed_step,
                failure_reason=failure_reason,
                reexecute=_reexecute,
                log_context=f"execute_plan_parallel layer {layer_idx}",
            )
            if replanned is not None:
                return replanned

            return ctx.result(plan_steps_completed=ctx.completed_count, status="failed")

    if ctx.long_run_task_id and ctx.last_status == "completed":
        from packages.agent.long_horizon import finalize_long_run_task_status

        await finalize_long_run_task_status(ctx.long_run_task_id, status="completed")

    return ctx.result(plan_steps_completed=total_steps)


async def run_plan_execution(
    *,
    parallel: bool,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: Any,
    step_system_messages: list[dict[str, Any]] | None,
    runner: Callable[..., Awaitable[dict[str, Any]]],
    max_replan_attempts: int,
    replan_attempt: int,
    plan_revisions: list[dict[str, Any]] | None,
    require_plan_approval: bool,
    long_run_task_id: str | None = None,
) -> dict[str, Any]:
    ctx, early = await PlanExecutionContext.create(
        plan=plan,
        tenant_id=tenant_id,
        session_id=session_id,
        allowed_tools=allowed_tools,
        allowed_models=allowed_models,
        model=model,
        session_store=session_store,
        step_system_messages=step_system_messages,
        runner=runner,
        max_replan_attempts=max_replan_attempts,
        replan_attempt=replan_attempt,
        plan_revisions=plan_revisions,
        require_plan_approval=require_plan_approval,
        long_run_task_id=long_run_task_id,
    )
    if early is not None:
        return early
    assert ctx is not None
    if parallel:
        return await execute_plan_parallel(ctx)
    return await execute_plan_serial(ctx)
