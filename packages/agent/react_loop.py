"""packages/agent/react_loop.py — ReAct 主循环（LLM ↔ tools）。

#172 PR-6a：从 runner.py 抽取；runner 仅负责 session / memory / billing wiring。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from packages.agent.context_budget import (
    ContextBudgetMeta,
    drop_oldest_until_budget,
    estimate_messages_tokens,
    truncate_tool_content,
)
from packages.agent.perf_metrics import get_agent_perf_metrics
from packages.agent.quality_gate import QUALITY_HINT, assess_tool_output
from packages.agent.reasoning import apply_cot_to_assistant_message
from packages.agent.registry import ToolRegistry
from packages.agent.risk import tool_requires_hitl
from packages.agent.shadow import shadow_tool_record
from packages.agent.tool_envelope import parse_tool_result, with_quality_hint
from packages.billing.recorder import record_upstream_usage
from packages.contracts.agent_schemas import ReasoningTraceRecord, ToolCallRecord
from packages.observability.context import get_trace_id

logger = logging.getLogger("ai_platform.agent.react_loop")


class AgentRunError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


async def _audit_tool_action(
    *,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    status: str,
    result_summary: str = "",
    approval_id: str | None = None,
    decided_by: str | None = None,
) -> None:
    try:
        import uuid

        from packages.audit.action_levels import get_classifier
        from packages.audit.action_logger import ActionAuditEntry, get_action_logger

        audit_logger = get_action_logger()
        if audit_logger is None:
            return
        classifier = get_classifier()
        level = classifier.classify(tool_name, arguments) if classifier else "unknown"
        entry = ActionAuditEntry(
            entry_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            session_id=session_id,
            tool_name=tool_name,
            action_level=level,
            arguments=arguments,
            result_summary=result_summary[:200],
            status=status,
            decided_by=decided_by,
            approval_id=approval_id,
        )
        await audit_logger.log_action(entry)
    except Exception as exc:
        logger.debug("audit log skipped: %s", exc)


async def execute_tool(
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
        await _audit_tool_action(
            tenant_id=tenant_id,
            session_id=session_id,
            tool_name=tool_name,
            arguments=args,
            status="pending",
            approval_id=approval.approval_id,
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
            env = parse_tool_result(result)
            if not env.ok and env.error_code == "AGENT_TOOL_FORBIDDEN":
                msg = (
                    env.data.get("message")
                    if isinstance(env.data, dict)
                    else str(env.data or "forbidden")
                )
                raise AgentRunError(
                    "AGENT_TOOL_FORBIDDEN",
                    msg,
                    detail={"tool_name": tool_name},
                )
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
            await _audit_tool_action(
                tenant_id=tenant_id,
                session_id=session_id,
                tool_name=tool_name,
                arguments=args,
                status="success",
                result_summary=str(result)[:200] if result else "",
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


@dataclass(frozen=True)
class ParsedToolCall:
    tc_id: str
    tool_name: str
    args_raw: str


@dataclass
class ToolRoundResult:
    tool_messages: list[dict[str, Any]]
    reflect_remaining: int
    runtime_truncated_tools: int
    fatal: AgentRunError | None


def _parse_tool_calls(tool_calls: list[Any]) -> list[ParsedToolCall]:
    parsed: list[ParsedToolCall] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        tool_name = fn.get("name")
        args_raw = fn.get("arguments") or "{}"
        tc_id = tc.get("id") or f"call_{len(parsed)}"
        if not isinstance(tool_name, str):
            continue
        parsed.append(ParsedToolCall(tc_id=tc_id, tool_name=tool_name, args_raw=str(args_raw)))
    return parsed


async def _execute_single_tool_call_raw(
    reg: ToolRegistry,
    parsed: ParsedToolCall,
    *,
    allowed_tools: tuple[str, ...],
    settings: Any,
    tenant_id: str,
    session_id: str,
    shadow_mode: bool,
) -> tuple[str | None, ToolCallRecord | None, AgentRunError | None]:
    try:
        result, record = await execute_tool(
            reg,
            tool_name=parsed.tool_name,
            arguments_json=parsed.args_raw,
            allowed_tools=allowed_tools,
            tool_timeout=settings.agent_tool_timeout_seconds,
            tool_max_retries=settings.agent_tool_max_retries,
            tenant_id=tenant_id,
            session_id=session_id,
            shadow_mode=shadow_mode,
        )
        return result, record, None
    except AgentRunError as e:
        if e.code == "AGENT_TOOL_FORBIDDEN":
            return None, None, e
        record = ToolCallRecord(
            tool_name=parsed.tool_name,
            arguments={},
            status="failed",
            result=None,
            error=f"{e.code}: {e.message}",
            latency_ms=0.0,
            quality_gate="failed",
        )
        return None, record, None
    except Exception as e:
        record = ToolCallRecord(
            tool_name=parsed.tool_name,
            arguments={},
            status="failed",
            result=None,
            error=str(e),
            latency_ms=0.0,
            quality_gate="failed",
        )
        return None, record, None


def _finalize_tool_call(
    parsed: ParsedToolCall,
    *,
    result: str | None,
    record: ToolCallRecord,
    settings: Any,
    reflect_remaining: int,
    runtime_truncated_tools: int,
    shadow_mode: bool,
    shadow_trace: list[ToolCallRecord],
    trace: list[ToolCallRecord],
) -> tuple[dict[str, Any], int, int]:
    if shadow_mode:
        shadow_trace.append(record)
    _, quality_gate = assess_tool_output(
        parsed.tool_name,
        result or "",
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
        "tool_call_id": parsed.tc_id,
        "content": tool_content,
    }
    return tool_msg, reflect_remaining, runtime_truncated_tools


async def process_tool_calls_round(
    tool_calls: list[Any],
    *,
    reg: ToolRegistry,
    allowed_tools: tuple[str, ...],
    settings: Any,
    tenant_id: str,
    session_id: str,
    shadow_mode: bool,
    strategy: str,
    reflect_remaining: int,
    runtime_truncated_tools: int,
    trace: list[ToolCallRecord],
    shadow_trace: list[ToolCallRecord],
) -> ToolRoundResult:
    parsed = _parse_tool_calls(tool_calls)
    tool_messages: list[dict[str, Any]] = []
    if not parsed:
        return ToolRoundResult(tool_messages, reflect_remaining, runtime_truncated_tools, None)

    if strategy == "parallel" and len(parsed) > 1:
        t0 = time.perf_counter()
        exec_pairs = await asyncio.gather(
            *(
                _execute_single_tool_call_raw(
                    reg,
                    p,
                    allowed_tools=allowed_tools,
                    settings=settings,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    shadow_mode=shadow_mode,
                )
                for p in parsed
            )
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        get_agent_perf_metrics().record_tool_parallel_batch(
            tenant_id=tenant_id,
            strategy=strategy,
            duration_ms=duration_ms,
            tool_count=len(parsed),
        )
        exec_results = list(zip(parsed, exec_pairs, strict=True))
    else:
        exec_results = []
        for p in parsed:
            raw = await _execute_single_tool_call_raw(
                reg,
                p,
                allowed_tools=allowed_tools,
                settings=settings,
                tenant_id=tenant_id,
                session_id=session_id,
                shadow_mode=shadow_mode,
            )
            exec_results.append((p, raw))

    for parsed_call, (result, record, fatal) in exec_results:
        if fatal is not None:
            return ToolRoundResult(tool_messages, reflect_remaining, runtime_truncated_tools, fatal)
        if record is None:
            continue
        if record.status == "success":
            tool_msg, reflect_remaining, runtime_truncated_tools = _finalize_tool_call(
                parsed_call,
                result=result,
                record=record,
                settings=settings,
                reflect_remaining=reflect_remaining,
                runtime_truncated_tools=runtime_truncated_tools,
                shadow_mode=shadow_mode,
                shadow_trace=shadow_trace,
                trace=trace,
            )
        else:
            trace.append(record)
            tool_msg = {
                "role": "tool",
                "tool_call_id": parsed_call.tc_id,
                "content": f"error: {record.error}",
            }
        tool_messages.append(tool_msg)

    return ToolRoundResult(tool_messages, reflect_remaining, runtime_truncated_tools, None)


@dataclass
class ReActLoopResult:
    final_message: str
    steps: int
    messages: list[dict[str, Any]]
    session_messages: list[dict[str, Any]]
    trace: list[ToolCallRecord]
    shadow_trace: list[ToolCallRecord]
    reasoning_trace: list[ReasoningTraceRecord]
    reflect_remaining: int
    runtime_truncated_tools: int
    budget_meta: ContextBudgetMeta
    resolved_model: str
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int


async def run_react_loop(
    *,
    messages: list[dict[str, Any]],
    session_messages: list[dict[str, Any]],
    registry: ToolRegistry,
    tools_spec: list[dict[str, Any]],
    resolved_model: str,
    model: str | None,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    settings: Any,
    shadow_mode: bool,
    active_reasoning_mode: str,
    active_tool_call_strategy: str,
    budget_meta: ContextBudgetMeta,
    pinned_prefix: int,
    reflect_remaining: int,
    runtime_truncated_tools: int = 0,
) -> ReActLoopResult:
    """LLM ↔ tools ReAct 循环，直到 assistant 返回最终文本或超步数。"""
    trace: list[ToolCallRecord] = []
    reasoning_trace: list[ReasoningTraceRecord] = []
    shadow_trace: list[ToolCallRecord] = []
    steps = 0
    final_message = ""
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    working_messages = list(messages)
    working_session_messages = list(session_messages)
    working_budget_meta = budget_meta
    working_reflect = reflect_remaining
    working_truncated = runtime_truncated_tools
    working_model = resolved_model

    while steps < settings.agent_max_steps:
        steps += 1
        if estimate_messages_tokens(working_messages) > settings.agent_context_token_budget:
            working_messages, dropped = drop_oldest_until_budget(
                working_messages,
                budget=settings.agent_context_token_budget,
                pinned_prefix=pinned_prefix,
            )
            if dropped:
                working_budget_meta = ContextBudgetMeta(
                    budget=working_budget_meta.budget,
                    estimated_tokens=estimate_messages_tokens(working_messages),
                    truncated_messages=working_budget_meta.truncated_messages + dropped,
                    truncated_tool_results=working_budget_meta.truncated_tool_results
                    + working_truncated,
                    summary_applied=working_budget_meta.summary_applied,
                    keep_recent_turns=working_budget_meta.keep_recent_turns,
                )

        payload: dict[str, Any] = {
            "model": working_model,
            "messages": working_messages,
            "temperature": 0.2,
        }
        if tools_spec:
            payload["tools"] = tools_spec
            payload["tool_choice"] = "auto"

        from packages.agent.runner import forward_with_model_router

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
            working_model = routed.model_used

        usage = record_upstream_usage(
            tenant_id=tenant_id,
            path="/v1/agent/run",
            model=working_model,
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
        if active_reasoning_mode == "cot":
            msg, thinking = apply_cot_to_assistant_message(msg)
            if thinking:
                get_agent_perf_metrics().record_cot_thinking_tokens(
                    tenant_id=tenant_id,
                    tokens=estimate_messages_tokens([{"role": "assistant", "content": thinking}]),
                )
            reasoning_trace.append(
                ReasoningTraceRecord(
                    step=steps,
                    thinking=thinking,
                    visible_content=msg.get("content")
                    if isinstance(msg.get("content"), str)
                    else None,
                )
            )
        finish = choice.get("finish_reason")
        working_messages.append(msg)
        working_session_messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if finish == "tool_calls" or tool_calls:
            if not isinstance(tool_calls, list) or not tool_calls:
                raise AgentRunError("AGENT_UPSTREAM_ERROR", "finish_reason=tool_calls 但无 tool_calls")

            round_result = await process_tool_calls_round(
                tool_calls,
                reg=registry,
                allowed_tools=allowed_tools,
                settings=settings,
                tenant_id=tenant_id,
                session_id=session_id,
                shadow_mode=shadow_mode,
                strategy=active_tool_call_strategy,
                reflect_remaining=working_reflect,
                runtime_truncated_tools=working_truncated,
                trace=trace,
                shadow_trace=shadow_trace,
            )
            if round_result.fatal is not None:
                raise round_result.fatal
            working_reflect = round_result.reflect_remaining
            working_truncated = round_result.runtime_truncated_tools
            for tool_msg in round_result.tool_messages:
                working_messages.append(tool_msg)
                working_session_messages.append(tool_msg)
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

    return ReActLoopResult(
        final_message=final_message,
        steps=steps,
        messages=working_messages,
        session_messages=working_session_messages,
        trace=trace,
        shadow_trace=shadow_trace,
        reasoning_trace=reasoning_trace,
        reflect_remaining=working_reflect,
        runtime_truncated_tools=working_truncated,
        budget_meta=working_budget_meta,
        resolved_model=working_model,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_tokens=total_tokens,
    )


# 向后兼容 runner 私有符号（tests / eval 仍 patch runner 模块）
_execute_tool = execute_tool
_process_tool_calls_round = process_tool_calls_round
