from __future__ import annotations

import json
import logging
from typing import Any

from packages.agent.context_budget import (
    ContextBudgetMeta,
    assemble_llm_messages,
    context_budget_platform_meta,
    context_strategy_platform_meta,
    estimate_messages_tokens,
    maybe_compact_session,
)
from packages.agent.context_compress import (
    inject_memory_into_messages,
    maybe_compact_with_llm,
    memory_injection_platform_meta,
    retrieve_and_inject_memory,
)
from packages.agent.hitl import ApprovalStatus, get_approval
from packages.agent.quality_gate import assess_tool_output
from packages.agent.react_loop import (
    AgentRunError,
    ReActLoopResult,
    _audit_tool_action,
    _execute_tool,
    _process_tool_calls_round,
    execute_tool,
    run_react_loop,
)
from packages.agent.reasoning import merge_cot_system_prompt, resolve_reasoning_mode
from packages.agent.registry import ToolRegistry
from packages.agent.session import SessionStore
from packages.agent.session_state import SessionState, count_user_messages
from packages.agent.tool_router import merge_pinned_tools, routing_meta, select_tools_from_messages
from packages.agent.tool_strategy import ToolCallStrategyError, resolve_tool_call_strategy
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.contracts.agent_schemas import ReasoningTraceRecord, ToolCallRecord
from packages.observability.context import get_trace_id

# Re-export：tests patch `packages.agent.runner.forward_with_model_router`
from packages.platform import (
    forward_with_model_router,  # noqa: F401
    get_settings,
    is_model_allowed,
)

logger = logging.getLogger("ai_platform.agent.runner")

__all__ = [
    "AgentRunError",
    "ToolCallRecord",
    "ReasoningTraceRecord",
    "ReActLoopResult",
    "run_agent",
    "resume_approved_tool",
    "run_react_loop",
    "execute_tool",
    "_audit_tool_action",
    "_execute_tool",
    "_process_tool_calls_round",
]


async def _maybe_persist_memory(
    *,
    tenant_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    turn_count: int,
) -> bool:
    try:
        from packages.memory import get_memory_store
        from packages.memory.store import MemoryRecord, _gen_id
        from packages.memory.summarize import summarize_messages

        store = get_memory_store()
        if store is None:
            return False
        summary = await summarize_messages(messages, tenant_id=tenant_id)
        if not summary.strip():
            return False
        record = MemoryRecord(
            memory_id=_gen_id(),
            tenant_id=tenant_id,
            scope="session",
            scope_id=session_id,
            content=summary,
            summary=None,
            metadata={
                "turn_count": turn_count,
                "trace_id": get_trace_id(),
                "source": "auto_summarize",
            },
        )
        await store.add(record)
        logger.info(
            "memory persisted tenant=%s session=%s turns=%d mem_id=%s",
            tenant_id,
            session_id,
            turn_count,
            record.memory_id,
        )
        return True
    except Exception as e:
        logger.warning("memory persist failed: %s", e)
        return False


async def resume_approved_tool(
    *,
    tenant_id: str,
    session_id: str,
    approval_id: str,
    allowed_tools: tuple[str, ...],
    session_store: SessionStore,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    reg = registry or ToolRegistry()
    approval = get_approval(approval_id)
    if approval is None:
        raise AgentRunError("AGENT_APPROVAL_INVALID", f"approval 不存在: {approval_id}")
    if approval.status != ApprovalStatus.confirmed:
        raise AgentRunError(
            "AGENT_APPROVAL_INVALID",
            f"approval 未确认: {approval.status}",
            detail={"approval_id": approval_id, "status": approval.status.value},
        )
    if approval.tenant_id != tenant_id or approval.session_id != session_id:
        raise AgentRunError("AGENT_APPROVAL_INVALID", "approval 与 tenant/session 不匹配")

    settings = get_settings()
    result, record = await execute_tool(
        reg,
        tool_name=approval.tool_name,
        arguments_json=json.dumps(approval.arguments, ensure_ascii=False),
        allowed_tools=allowed_tools,
        tool_timeout=settings.agent_tool_timeout_seconds,
        tool_max_retries=settings.agent_tool_max_retries,
        tenant_id=tenant_id,
        session_id=session_id,
        skip_hitl=True,
    )
    _, quality_gate = assess_tool_output(
        approval.tool_name,
        result,
        min_score=settings.agent_quality_min_score,
    )
    record = record.model_copy(update={"quality_gate": quality_gate})
    await _audit_tool_action(
        tenant_id=tenant_id,
        session_id=session_id,
        tool_name=approval.tool_name,
        arguments=approval.arguments,
        status="success",
        result_summary=str(result)[:200] if result else "",
        approval_id=approval_id,
        decided_by=approval.reviewer,
    )

    state = session_store.get_session_state(tenant_id, session_id)
    tool_msg = {"role": "tool", "tool_call_id": f"resume_{approval_id[:8]}", "content": result}
    saved = SessionState(
        messages=[*state.messages, tool_msg],
        summary=state.summary,
        turn_count=state.turn_count,
    )
    session_store.save_session_state(tenant_id, session_id, saved)

    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": "已执行经人工确认的工具调用",
        "tool_calls": [record],
        "steps": 0,
        "model": settings.agent_model or settings.default_model,
        "trace_id": get_trace_id(),
        "status": "completed",
        "approval_id": approval_id,
    }


def _build_run_payload(
    *,
    tenant_id: str,
    session_id: str,
    loop: ReActLoopResult,
    saved_state: SessionState,
    shadow_mode: bool,
    active_reasoning_mode: str,
    active_tool_call_strategy: str,
    routing: Any,
    memory_injection: Any,
    memory_persisted: bool,
    token_budget_daily: int,
    token_budget_monthly: int,
) -> dict[str, Any]:
    snap = get_budget_snapshot(
        tenant_id,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
    budget_meta = ContextBudgetMeta(
        budget=loop.budget_meta.budget,
        estimated_tokens=estimate_messages_tokens(loop.messages),
        truncated_messages=loop.budget_meta.truncated_messages,
        truncated_tool_results=loop.budget_meta.truncated_tool_results
        + loop.runtime_truncated_tools,
        summary_applied=loop.budget_meta.summary_applied,
        keep_recent_turns=loop.budget_meta.keep_recent_turns,
    )
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": loop.final_message,
        "tool_calls": loop.trace if not shadow_mode else [],
        "steps": loop.steps,
        "model": loop.resolved_model,
        "trace_id": get_trace_id(),
        "status": "completed",
        "reasoning_mode": active_reasoning_mode,
        "tool_call_strategy": active_tool_call_strategy,
    }
    if loop.reasoning_trace:
        payload["reasoning_trace"] = loop.reasoning_trace
    if shadow_mode and loop.shadow_trace:
        payload["shadow_tool_calls"] = loop.shadow_trace
    platform_meta: dict[str, Any] = {
        "tool_routing": routing_meta(routing),
        "tool_call_strategy": active_tool_call_strategy,
        "context_budget": context_budget_platform_meta(budget_meta),
        "context_strategy": context_strategy_platform_meta(),
        "session_turn_count": saved_state.turn_count,
        "session_summary": bool(saved_state.summary),
        "reflect_remaining": loop.reflect_remaining,
        "shadow_mode": shadow_mode,
        "memory_persisted": memory_persisted,
    }
    if memory_injection is not None:
        platform_meta["memory_injection"] = memory_injection_platform_meta(memory_injection)
    if loop.total_tokens > 0:
        platform_meta["usage"] = {
            "input_tokens": loop.total_input_tokens,
            "output_tokens": loop.total_output_tokens,
            "total_tokens": loop.total_tokens,
            **budget_platform_meta(snap, loop.total_tokens),
        }
    payload["_platform"] = platform_meta
    return payload


async def _run_resumed_agent(
    *,
    resume_ctx: Any,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: SessionStore,
    registry: ToolRegistry,
    shadow_mode: bool,
    settings: Any,
    token_budget_daily: int,
    token_budget_monthly: int,
) -> dict[str, Any]:
    """Run the agent by injecting a ReactResumeContext into run_react_loop.

    This skips normal message assembly, memory injection, and tool routing
    because the resume context already contains the fully reconstructed state
    from the previous checkpoint.
    """
    from packages.agent.react_loop import run_react_loop as _run_react_loop

    # Build tools_spec (needed for LLM call)
    tool_names = [t for t in registry.list_tools() if t in allowed_tools]
    tools_spec = registry.openai_tools_spec_subset(
        tool_names if tool_names else allowed_tools,
        allowed_tools,
    )

    # Init guardrails (same machinery as normal path)
    progress_tracker = None
    threshold_enforcer = None
    if settings.agent_guardrail_enabled:
        import json

        from packages.agent.guardrails import (
            AgentGuardrailConfig,
            ProgressTracker,
            ThresholdEnforcer,
        )

        tool_limits_raw = getattr(settings, "agent_guardrail_tool_call_limits", "{}")
        try:
            tool_limits = json.loads(tool_limits_raw)
        except (json.JSONDecodeError, TypeError):
            tool_limits = {}
        guardrail_config = AgentGuardrailConfig(
            enabled=settings.agent_guardrail_enabled,
            max_tool_calls_total=settings.agent_guardrail_max_tool_calls_total,
            tool_call_limits=tool_limits,
            agent_timeout_seconds=settings.agent_guardrail_timeout_seconds,
            max_consecutive_empty_tools=settings.agent_guardrail_max_consecutive_empty,
            max_consecutive_identical_calls=settings.agent_guardrail_max_consecutive_identical,
            convergence_strategy=settings.agent_guardrail_convergence_strategy,
        )
        progress_tracker = ProgressTracker(
            max_consecutive_empty_tools=guardrail_config.max_consecutive_empty_tools,
            max_consecutive_identical_calls=guardrail_config.max_consecutive_identical_calls,
        )
        threshold_enforcer = ThresholdEnforcer(
            max_tool_calls_total=guardrail_config.max_tool_calls_total,
            tool_call_limits=guardrail_config.tool_call_limits,
            agent_timeout_seconds=guardrail_config.agent_timeout_seconds,
        )

    # Run the react loop with restored state
    loop = await _run_react_loop(
        messages=resume_ctx.messages,
        session_messages=resume_ctx.session_messages,
        registry=registry,
        tools_spec=tools_spec,
        resolved_model=resume_ctx.resolved_model or (model or settings.agent_model or settings.default_model),
        model=model,
        tenant_id=tenant_id,
        session_id=session_id,
        allowed_tools=allowed_tools,
        settings=settings,
        shadow_mode=shadow_mode,
        active_reasoning_mode=settings.agent_reasoning_mode or "",
        active_tool_call_strategy=settings.agent_tool_call_strategy or "",
        budget_meta=resume_ctx.budget_meta,
        pinned_prefix=0,
        reflect_remaining=resume_ctx.reflect_remaining,
        runtime_truncated_tools=resume_ctx.runtime_truncated_tools,
        progress_tracker=progress_tracker,
        threshold_enforcer=threshold_enforcer,
        guardrail_config=guardrail_config,
    )

    # Merge prior trace + reasoning_trace with new loop result
    all_trace: list[ToolCallRecord] = list(resume_ctx.trace) + list(loop.trace)
    all_reasoning: list[ReasoningTraceRecord] = (
        list(resume_ctx.reasoning_trace) + list(loop.reasoning_trace)
    )
    merged_messages: list[dict[str, Any]] = (
        list(resume_ctx.messages) + list(loop.messages)[len(resume_ctx.messages):]
    )
    merged_session_messages: list[dict[str, Any]] = (
        list(resume_ctx.session_messages)
        + list(loop.session_messages)[len(resume_ctx.session_messages):]
    )

    # Build a synthetic ReActLoopResult with merged traces
    merged_loop = ReActLoopResult(
        final_message=loop.final_message,
        steps=resume_ctx.resume_step + loop.steps,
        messages=merged_messages,
        session_messages=merged_session_messages,
        trace=all_trace,
        shadow_trace=loop.shadow_trace,
        reasoning_trace=all_reasoning,
        reflect_remaining=loop.reflect_remaining,
        runtime_truncated_tools=loop.runtime_truncated_tools,
        budget_meta=loop.budget_meta,
        resolved_model=loop.resolved_model,
        total_input_tokens=loop.total_input_tokens,
        total_output_tokens=loop.total_output_tokens,
        total_tokens=loop.total_tokens,
    )

    # Session state save
    state = session_store.get_session_state(tenant_id, session_id)
    saved_state = SessionState(
        messages=merged_loop.session_messages,
        summary=state.summary,
        turn_count=state.turn_count + 1,
    )
    if settings.context_llm_summary_enabled:
        saved_state = await maybe_compact_with_llm(
            saved_state,
            every_n_turns=settings.agent_summary_every_n_turns,
            keep_recent_turns=settings.agent_context_keep_recent_turns,
            tenant_id=tenant_id,
            enable_llm_summary=settings.context_llm_summary_enabled,
        )
    else:
        saved_state = maybe_compact_session(
            saved_state,
            every_n_turns=settings.agent_summary_every_n_turns,
            keep_recent_turns=settings.agent_context_keep_recent_turns,
        )
    session_store.save_session_state(tenant_id, session_id, saved_state)

    memory_persisted = False
    if (
        settings.memory_store_enabled
        and saved_state.turn_count > 0
        and saved_state.turn_count % max(1, settings.memory_summarize_every_n_turns) == 0
    ):
        memory_persisted = await _maybe_persist_memory(
            tenant_id=tenant_id,
            session_id=session_id,
            messages=saved_state.messages,
            turn_count=saved_state.turn_count,
        )

    logger.info(
        "agent_run (resumed)",
        extra={
            "trace_id": get_trace_id(),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "steps": merged_loop.steps,
            "tool_calls": len(all_trace),
            "resume_step": resume_ctx.resume_step,
        },
    )

    return _build_run_payload(
        tenant_id=tenant_id,
        session_id=session_id,
        loop=merged_loop,
        saved_state=saved_state,
        shadow_mode=shadow_mode,
        active_reasoning_mode=settings.agent_reasoning_mode or "",
        active_tool_call_strategy=settings.agent_tool_call_strategy or "",
        routing=None,
        memory_injection=None,
        memory_persisted=memory_persisted,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )

async def run_agent(
    *,
    tenant_id: str,
    session_id: str,
    new_messages: list[dict[str, Any]],
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: SessionStore,
    registry: ToolRegistry | None = None,
    token_budget_daily: int = -1,
    token_budget_monthly: int = -1,
    shadow_mode: bool = False,
    approval_id: str | None = None,
    reasoning_mode: str | None = None,
    tool_call_strategy: str | None = None,
    pinned_tools: tuple[str, ...] | None = None,
    long_run_task_id: str | None = None,
) -> dict[str, Any]:
    if approval_id:
        return await resume_approved_tool(
            tenant_id=tenant_id,
            session_id=session_id,
            approval_id=approval_id,
            allowed_tools=allowed_tools,
            session_store=session_store,
            registry=registry,
        )

    settings = get_settings()

    # ------------------------------------------------------------------ #
    # Resume path: inject checkpoint state directly into run_react_loop
    # ------------------------------------------------------------------ #
    if long_run_task_id is not None:
        from packages.agent.react_resume_loader import load_react_resume_context

        resume_ctx = await load_react_resume_context(long_run_task_id)
        if resume_ctx is not None:
            return await _run_resumed_agent(
                resume_ctx=resume_ctx,
                tenant_id=tenant_id,
                session_id=session_id,
                allowed_tools=allowed_tools,
                allowed_models=allowed_models,
                model=model,
                session_store=session_store,
                registry=registry or ToolRegistry(),
                shadow_mode=shadow_mode,
                settings=settings,
                token_budget_daily=token_budget_daily,
                token_budget_monthly=token_budget_monthly,
            )

    # Fall-through: no resume context found, continue with normal flow
    try:
        active_reasoning_mode = resolve_reasoning_mode(
            reasoning_mode, settings.agent_reasoning_mode
        )
    except ValueError as e:
        raise AgentRunError("AGENT_INVALID_REASONING_MODE", str(e)) from e
    try:
        active_tool_call_strategy = resolve_tool_call_strategy(
            tool_call_strategy, settings.agent_tool_call_strategy
        )
    except ToolCallStrategyError as e:
        raise AgentRunError("AGENT_INVALID_TOOL_CALL_STRATEGY", str(e)) from e
    reg = registry or ToolRegistry()
    allowed, resolved_model = is_model_allowed(
        model or settings.agent_model,
        tenant_default=None,
        allowed_models=allowed_models,
    )
    if not allowed:
        raise AgentRunError(
            "MODEL_NOT_ALLOWED",
            f"模型不在白名单: {model or settings.agent_model or settings.default_model}",
            detail={"allowed_models": list(allowed_models), "resolved_model": resolved_model},
        )

    state = session_store.get_session_state(tenant_id, session_id)
    state.turn_count += count_user_messages(new_messages)
    session_messages: list[dict[str, Any]] = [*state.messages, *new_messages]

    messages, budget_meta = assemble_llm_messages(
        SessionState(messages=state.messages, summary=state.summary, turn_count=state.turn_count),
        new_messages,
        budget=settings.agent_context_token_budget,
        keep_recent_turns=settings.agent_context_keep_recent_turns,
        tool_result_max_chars=settings.agent_tool_result_max_chars,
    )
    if active_reasoning_mode == "cot":
        messages = merge_cot_system_prompt(messages)

    memory_injection = None
    if settings.context_memory_injection_enabled and new_messages:
        query_text = ""
        for m in reversed(new_messages):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    query_text = content
                    break
        budget_remaining = max(0, budget_meta.budget - budget_meta.estimated_tokens)
        if budget_remaining >= settings.context_memory_injection_min_budget:
            memory_injection = await retrieve_and_inject_memory(
                tenant_id=tenant_id,
                session_id=session_id,
                query=query_text,
                budget_remaining=budget_remaining,
                top_k=settings.context_memory_injection_top_k,
                scope="session",
            )
            if memory_injection.injected:
                messages = inject_memory_into_messages(messages, memory_injection)

    routing = select_tools_from_messages(
        session_messages,
        registry=reg,
        allowed_tools=allowed_tools,
        routing_enabled=settings.agent_tool_routing_enabled,
        rag_enabled=settings.agent_tool_rag_enabled,
    )
    tool_names = merge_pinned_tools(
        routing,
        registry=reg,
        allowed_tools=allowed_tools,
        pinned_tools=pinned_tools,
    )
    tools_spec = reg.openai_tools_spec_subset(tool_names, allowed_tools)
    pinned_prefix = 1 if budget_meta.summary_applied else 0

    # Initialize guardrails
    progress_tracker = None
    threshold_enforcer = None
    guardrail_config = None
    if settings.agent_guardrail_enabled:
        import json

        from packages.agent.guardrails import (
            AgentGuardrailConfig,
            ProgressTracker,
            ThresholdEnforcer,
        )

        tool_limits_raw = getattr(settings, "agent_guardrail_tool_call_limits", "{}")
        try:
            tool_limits = json.loads(tool_limits_raw)
        except (json.JSONDecodeError, TypeError):
            tool_limits = {}
        guardrail_config = AgentGuardrailConfig(
            enabled=settings.agent_guardrail_enabled,
            max_tool_calls_total=settings.agent_guardrail_max_tool_calls_total,
            tool_call_limits=tool_limits,
            agent_timeout_seconds=settings.agent_guardrail_timeout_seconds,
            max_consecutive_empty_tools=settings.agent_guardrail_max_consecutive_empty,
            max_consecutive_identical_calls=settings.agent_guardrail_max_consecutive_identical,
            convergence_strategy=settings.agent_guardrail_convergence_strategy,
        )
        progress_tracker = ProgressTracker(
            max_consecutive_empty_tools=guardrail_config.max_consecutive_empty_tools,
            max_consecutive_identical_calls=guardrail_config.max_consecutive_identical_calls,
        )
        threshold_enforcer = ThresholdEnforcer(
            max_tool_calls_total=guardrail_config.max_tool_calls_total,
            tool_call_limits=guardrail_config.tool_call_limits,
            agent_timeout_seconds=guardrail_config.agent_timeout_seconds,
        )

    loop = await run_react_loop(
        messages=messages,
        session_messages=session_messages,
        registry=reg,
        tools_spec=tools_spec,
        resolved_model=resolved_model,
        model=model,
        tenant_id=tenant_id,
        session_id=session_id,
        allowed_tools=allowed_tools,
        settings=settings,
        shadow_mode=shadow_mode,
        active_reasoning_mode=active_reasoning_mode,
        active_tool_call_strategy=active_tool_call_strategy,
        budget_meta=budget_meta,
        pinned_prefix=pinned_prefix,
        reflect_remaining=settings.agent_reflect_max_retries,
        progress_tracker=progress_tracker,
        threshold_enforcer=threshold_enforcer,
        guardrail_config=guardrail_config,
    )

    saved_state = SessionState(
        messages=loop.session_messages,
        summary=state.summary,
        turn_count=state.turn_count,
    )
    if settings.context_llm_summary_enabled:
        saved_state = await maybe_compact_with_llm(
            saved_state,
            every_n_turns=settings.agent_summary_every_n_turns,
            keep_recent_turns=settings.agent_context_keep_recent_turns,
            tenant_id=tenant_id,
            enable_llm_summary=settings.context_llm_summary_enabled,
        )
    else:
        saved_state = maybe_compact_session(
            saved_state,
            every_n_turns=settings.agent_summary_every_n_turns,
            keep_recent_turns=settings.agent_context_keep_recent_turns,
        )
    session_store.save_session_state(tenant_id, session_id, saved_state)

    memory_persisted = False
    if (
        settings.memory_store_enabled
        and saved_state.turn_count > 0
        and saved_state.turn_count % max(1, settings.memory_summarize_every_n_turns) == 0
    ):
        memory_persisted = await _maybe_persist_memory(
            tenant_id=tenant_id,
            session_id=session_id,
            messages=saved_state.messages,
            turn_count=saved_state.turn_count,
        )

    logger.info(
        "agent_run",
        extra={
            "trace_id": get_trace_id(),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "steps": loop.steps,
            "tool_calls": len(loop.trace),
        },
    )

    return _build_run_payload(
        tenant_id=tenant_id,
        session_id=session_id,
        loop=loop,
        saved_state=saved_state,
        shadow_mode=shadow_mode,
        active_reasoning_mode=active_reasoning_mode,
        active_tool_call_strategy=active_tool_call_strategy,
        routing=routing,
        memory_injection=memory_injection,
        memory_persisted=memory_persisted,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
