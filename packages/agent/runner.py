from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from apps.gateway.model_router import forward_with_model_router, is_model_allowed
from apps.gateway.settings import get_settings
from packages.agent.registry import ToolRegistry
from packages.agent.session import SessionStore
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
        except asyncio.TimeoutError:
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
) -> dict[str, Any]:
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

    history = session_store.get_messages(tenant_id, session_id)
    messages: list[dict[str, Any]] = [*history, *new_messages]
    tools_spec = reg.openai_tools_spec(allowed_tools)
    trace: list[ToolCallRecord] = []
    steps = 0
    final_message = ""

    while steps < settings.agent_max_steps:
        steps += 1
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

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AgentRunError("AGENT_UPSTREAM_ERROR", "upstream 无 choices")

        choice = choices[0]
        msg = _extract_message(choice)
        finish = choice.get("finish_reason")
        messages.append(msg)

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
                    )
                    trace.append(record)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": result or record.error or "",
                        }
                    )
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
                    )
                    trace.append(record)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": f"error: {e.message}",
                        }
                    )
                except Exception as e:
                    record = ToolCallRecord(
                        tool_name=tool_name,
                        arguments={},
                        status="failed",
                        result=None,
                        error=str(e),
                        latency_ms=0.0,
                    )
                    trace.append(record)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": f"error: {e}",
                        }
                    )
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

    session_store.save_messages(tenant_id, session_id, messages)

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

    return {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "final_message": final_message,
        "tool_calls": trace,
        "steps": steps,
        "model": resolved_model,
        "trace_id": get_trace_id(),
    }
