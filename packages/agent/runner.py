from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from apps.gateway.model_router import forward_with_model_router, is_model_allowed
from apps.gateway.settings import get_settings
from packages.agent.context_budget import (
    ContextBudgetMeta,
    assemble_llm_messages,
    context_budget_platform_meta,
    drop_oldest_until_budget,
    estimate_messages_tokens,
    maybe_compact_session,
    truncate_tool_content,
)
from packages.agent.hitl import ApprovalStatus, get_approval
from packages.agent.quality_gate import QUALITY_HINT, assess_tool_output
from packages.agent.registry import ToolRegistry
from packages.agent.risk import tool_requires_hitl
from packages.agent.session import SessionStore
from packages.agent.session_state import SessionState, count_user_messages
from packages.agent.shadow import shadow_tool_record
from packages.agent.tool_envelope import with_quality_hint
from packages.agent.tool_router import routing_meta, select_tools_from_messages
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.billing.recorder import record_upstream_usage
from packages.contracts.agent_schemas import ToolCallRecord
from packages.observability.context import get_trace_id

logger = logging.getLogger("ai_platform.agent.runner")


class AgentRunError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


async def _execute_tool(
    registry: ToolRegistry,
    *,
    tool_name: str,
    arguments_json: str,
    allowed_tools: tuple[str, ...],
    tool_timeout: float,
    tool_max_retries: int,
    tenant_id: str = "",
    session_id: str = "",
    shadow_mode: bool = False,
    skip_hitl: bool = False,
) -> tuple[str, ToolCallRecord]:
    started = time.perf_counter()
    if not registry.is_allowed(tool_name, allowed_tools):
        elapsed = (time.perf_counter() - started) * 1000
        record = ToolCallRecord(
            tool_name=tool_name,
            arguments={},
            status="forbidden",
            result=None,
            error="租户无权使用该工具",
            latency_ms=round(elapsed, 2),
        )
        raise AgentRunError(
            "AGENT_TOOL_FORBIDDEN",
            f"工具未授权: {tool_name}",
            detail={"tool_name": tool_name},
        )

    tool = registry.get(tool_name)
    if not tool:
        raise AgentRunError("AGENT_TOOL_NOT_FOUND", f"未知工具: {tool_name}")

    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        raise AgentRunError("AGENT_TOOL_BAD_ARGS", f"工具参数非 JSON: {e}") from e
    if not isinstance(args, dict):
        raise AgentRunError("AGENT_TOOL_BAD_ARGS", "工具参数须为 JSON 对象")

    if shadow_mode:
        return shadow_tool_record(tool_name=tool_name, arguments=args)

    if not skip_hitl and tool_requires_hitl(tool_name):
        from packages.agent.hitl import create_pending_execution

        approval = create_pending_execution(
            tenant_id=tenant_id,
            session_id=session_id,
            tool_name=tool_name,
            arguments=args,
        )
        raise AgentRunError(
            "AGENT_PENDING_APPROVAL",
            f"高风险工具需人工确认: {tool_name}",
            detail={
                "approval_id": approval.approval_id,
                "tool_name": tool_name,
                "arguments": args,
            },
        )

    last_err: str | None = None
    for attempt in range(tool_max_retries + 1):
        try:
            t0 = time.perf_counter()
            result = await asyncio.wait_for(tool.handler(args), timeout=tool_timeout)
            elapsed = (time.perf_counter() - t0) * 1000
            record = ToolCallRecord(
                tool_name=tool_name,
                arguments=args,
                status="success",
                result=result,
                error=None,
                latency_ms=round(elapsed, 2),
                attempt=attempt,
            )
            return result, record
        except TimeoutError:
            last_err = f"工具执行超时（>{tool_timeout}s）"
        except AgentRunError:
            raise
        except Exception as e:
            last_err = str(e)

    elapsed = (time.perf_counter() - started) * 1000
    record = ToolCallRecord(
        tool_name=tool_name,
        arguments=args,
        status="failed",
        result=None,
        error=last_err,
        latency_ms=round(elapsed, 2),
        attempt=tool_max_retries,
    )
    return "", record


def _extract_message(choice: dict[str, Any]) -> dict[str, Any]:
    msg = choice.get("message")
    if not isinstance(msg, dict):
        raise AgentRunError("AGENT_UPSTREAM_ERROR", "upstream message 格式错误")
    return msg


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
    result, record = await _execute_tool(
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
    runtime_truncated_tools = 0
    reflect_remaining = settings.agent_reflect_max_retries

    routing = select_tools_from_messages(
        session_messages,
        registry=reg,
        allowed_tools=allowed_tools,
        routing_enabled=settings.agent_tool_routing_enabled,
        rag_enabled=settings.agent_tool_rag_enabled,
    )
    tools_spec = reg.openai_tools_spec_subset(routing.tool_names, allowed_tools)
    trace: list[ToolCallRecord] = []
    shadow_trace: list[ToolCallRecord] = []
    steps = 0
    final_message = ""
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0

    pinned_prefix = 1 if budget_meta.summary_applied else 0

    while steps < settings.agent_max_steps:
        steps += 1
        if estimate_messages_tokens(messages) > settings.agent_context_token_budget:
            messages, dropped = drop_oldest_until_budget(
                messages,
                budget=settings.agent_context_token_budget,
                pinned_prefix=pinned_prefix,
            )
            if dropped:
                budget_meta = ContextBudgetMeta(
                    budget=budget_meta.budget,
                    estimated_tokens=estimate_messages_tokens(messages),
                    truncated_messages=budget_meta.truncated_messages + dropped,
                    truncated_tool_results=budget_meta.truncated_tool_results + runtime_truncated_tools,
                    summary_applied=budget_meta.summary_applied,
                    keep_recent_turns=budget_meta.keep_recent_turns,
                )

        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools_spec:
            payload["tools"] = tools_spec
            payload["tool_choice"] = "auto"

        routed = await forward_with_model_router(
            payload,
            requested_model=model or settings.agent_model,
        )
        body = routed.body
        if routed.error and body is None:
            raise AgentRunError(
                "AGENT_UPSTREAM_ERROR",
                routed.error,
                detail={"status": routed.status, "models_tried": list(routed.models_tried)},
            )
        if body is None or not (200 <= routed.status < 300):
            raise AgentRunError(
                "AGENT_UPSTREAM_ERROR",
                f"upstream status {routed.status}",
                detail={"upstream": body, "models_tried": list(routed.models_tried)},
            )
        if routed.model_used:
            resolved_model = routed.model_used

        usage = record_upstream_usage(
            tenant_id=tenant_id,
            path="/v1/agent/run",
            model=resolved_model,
            upstream_body=body,
            trace_id=get_trace_id(),
        )
        if usage is not None:
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens
            total_tokens += usage.total_tokens

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgentRunError("AGENT_UPSTREAM_ERROR", "upstream 无 choices")

        choice = choices[0]
        msg = _extract_message(choice)
        finish = choice.get("finish_reason")
        messages.append(msg)
        session_messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if finish == "tool_calls" or tool_calls:
            if not isinstance(tool_calls, list) or not tool_calls:
                raise AgentRunError("AGENT_UPSTREAM_ERROR", "finish_reason=tool_calls 但无 tool_calls")

            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                tool_name = fn.get("name")
                args_raw = fn.get("arguments") or "{}"
                tc_id = tc.get("id") or f"call_{len(trace)}"

                if not isinstance(tool_name, str):
                    continue

                try:
                    result, record = await _execute_tool(
                        reg,
                        tool_name=tool_name,
                        arguments_json=str(args_raw),
                        allowed_tools=allowed_tools,
                        tool_timeout=settings.agent_tool_timeout_seconds,
                        tool_max_retries=settings.agent_tool_max_retries,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        shadow_mode=shadow_mode,
                    )
                    if shadow_mode:
                        shadow_trace.append(record)
                    _, quality_gate = assess_tool_output(
                        tool_name,
                        result,
                        min_score=settings.agent_quality_min_score,
                    )
                    record = record.model_copy(update={"quality_gate": quality_gate})
                    trace.append(record)
                    tool_content = result or record.error or ""
                    if quality_gate == "low_quality" and reflect_remaining > 0:
                        reflect_remaining -= 1
                        tool_content = with_quality_hint(tool_content, QUALITY_HINT)
                    tool_content, did_trunc = truncate_tool_content(
                        tool_content,
                        settings.agent_tool_result_max_chars,
                    )
                    if did_trunc:
                        runtime_truncated_tools += 1
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_content,
                    }
                    messages.append(tool_msg)
                    session_messages.append(tool_msg)
                except AgentRunError as e:
                    if e.code == "AGENT_TOOL_FORBIDDEN":
                        raise
                    record = ToolCallRecord(
                        tool_name=tool_name,
                        arguments={},
                        status="failed",
                        result=None,
                        error=e.message,
                        latency_ms=0.0,
                        quality_gate="failed",
                    )
                    trace.append(record)
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"error: {e.message}",
                    }
                    messages.append(tool_msg)
                    session_messages.append(tool_msg)
                except Exception as e:
                    record = ToolCallRecord(
                        tool_name=tool_name,
                        arguments={},
                        status="failed",
                        result=None,
                        error=str(e),
                        latency_ms=0.0,
                        quality_gate="failed",
                    )
                    trace.append(record)
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": f"error: {e}",
                    }
                    messages.append(tool_msg)
                    session_messages.append(tool_msg)
            continue

        content = msg.get("content")
        final_message = content.strip() if isinstance(content, str) else ""
        break
    else:
        raise AgentRunError(
            "AGENT_MAX_STEPS",
            f"超过最大步数 {settings.agent_max_steps}",
            detail={"steps": steps},
        )

    saved_state = SessionState(
        messages=session_messages,
        summary=state.summary,
        turn_count=state.turn_count,
    )
    saved_state = maybe_compact_session(
        saved_state,
        every_n_turns=settings.agent_summary_every_n_turns,
        keep_recent_turns=settings.agent_context_keep_recent_turns,
    )
    session_store.save_session_state(tenant_id, session_id, saved_state)

    logger.info(
        "agent_run",
        extra={
            "trace_id": get_trace_id(),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "steps": steps,
            "tool_calls": len(trace),
        },
    )

    snap = get_budget_snapshot(
        tenant_id,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": final_message,
        "tool_calls": trace if not shadow_mode else [],
        "steps": steps,
        "model": resolved_model,
        "trace_id": get_trace_id(),
        "status": "completed",
    }
    if shadow_mode and shadow_trace:
        payload["shadow_tool_calls"] = shadow_trace
    budget_meta = ContextBudgetMeta(
        budget=budget_meta.budget,
        estimated_tokens=estimate_messages_tokens(messages),
        truncated_messages=budget_meta.truncated_messages,
        truncated_tool_results=budget_meta.truncated_tool_results + runtime_truncated_tools,
        summary_applied=budget_meta.summary_applied,
        keep_recent_turns=budget_meta.keep_recent_turns,
    )
    platform_meta: dict[str, Any] = {
        "tool_routing": routing_meta(routing),
        "context_budget": context_budget_platform_meta(budget_meta),
        "session_turn_count": saved_state.turn_count,
        "session_summary": bool(saved_state.summary),
        "reflect_remaining": reflect_remaining,
        "shadow_mode": shadow_mode,
    }
    if total_tokens > 0:
        platform_meta["usage"] = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            **budget_platform_meta(snap, total_tokens),
        }
    payload["_platform"] = platform_meta
    return payload
