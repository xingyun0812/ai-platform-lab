"""Agent 委托逻辑 — 主 Agent 调用子 Agent 执行任务。

委托流程：
    1. 主 Agent 通过 agent_call 节点指定 sub_agent_id + task
    2. 子 Agent 通过完整 ``run_agent()`` ReAct 循环执行任务
    3. 结果写入共享黑板并返回给主 Agent

防递归：
    - 委托深度限制（max_delegation_depth）
    - 委托栈跟踪（防止 A→B→A 循环）
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.agent.multi_agent.blackboard import (
    BlackboardStore,
    format_entries_for_reviewer,
    get_blackboard,
)
from packages.agent.multi_agent.registry import (
    get_agent_registry,
)

logger = logging.getLogger("ai_platform.multi_agent.delegation")


class DelegationError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


@dataclass
class DelegationResult:
    """委托结果。"""

    agent_id: str
    task: str
    status: str  # completed | failed | timeout
    output: str
    usage: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    execution_time_ms: float = 0.0
    delegation_depth: int = 0
    blackboard_entry_id: str | None = None
    sub_session_id: str | None = None


def resolve_delegation_tools(
    spec_tools: list[str],
    tenant_allowed: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """AgentSpec 工具白名单与租户 ACL 求交；空 spec 列表表示全部（仍受租户约束）。"""
    if tenant_allowed:
        tenant_set = set(tenant_allowed)
        if spec_tools:
            return tuple(t for t in spec_tools if t in tenant_set)
        return tenant_allowed
    if spec_tools:
        return tuple(spec_tools)
    return ()


def _build_delegation_messages(
    *,
    spec,
    task: str,
    inputs: dict[str, Any] | None,
    blackboard_text: str | None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    if spec.system_prompt:
        system_parts.append(spec.system_prompt.strip())
    if blackboard_text:
        system_parts.append(f"共享黑板（其他 Agent 输出）:\n{blackboard_text}")
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    context_parts: list[str] = []
    if inputs:
        for k, v in inputs.items():
            context_parts.append(f"{k}: {v}")
    user_content = task
    if context_parts:
        user_content = "\n".join(context_parts) + "\n\n任务：\n" + task
    messages.append({"role": "user", "content": user_content})
    return messages


async def delegate_to_agent(
    *,
    agent_id: str,
    task: str,
    tenant_id: str = "admin",
    session_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    delegation_stack: list[str] | None = None,
    max_depth: int | None = None,
    timeout_seconds: float = 60.0,
    allowed_tools: tuple[str, ...] | None = None,
    allowed_models: tuple[str, ...] | None = None,
    use_blackboard: bool = True,
    blackboard: BlackboardStore | None = None,
) -> DelegationResult:
    """委托任务给指定 Agent（完整 Runner + 可选黑板）。"""
    start_time = time.time()
    stack = list(delegation_stack or [])
    registry = get_agent_registry()
    if registry is None:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error="MULTI_AGENT_DISABLED",
            delegation_depth=len(stack),
        )
    spec = registry.get_agent(agent_id)
    if spec is None:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=f"AGENT_NOT_FOUND: {agent_id}",
            delegation_depth=len(stack),
        )
    if not spec.enabled:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=f"AGENT_DISABLED: {agent_id}",
            delegation_depth=len(stack),
        )
    if not spec.can_be_delegated_to:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=f"AGENT_NOT_DELEGATABLE: {agent_id}",
            delegation_depth=len(stack),
        )
    if agent_id in stack:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=f"DELEGATION_CYCLE: {' → '.join(stack + [agent_id])}",
            delegation_depth=len(stack),
        )
    effective_max_depth = max_depth if max_depth is not None else spec.max_delegation_depth
    if len(stack) >= effective_max_depth:
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=f"MAX_DEPTH_EXCEEDED: {len(stack)} >= {effective_max_depth}",
            delegation_depth=len(stack),
        )

    stack.append(agent_id)
    registry.mark_invoked(agent_id)
    bb = blackboard or get_blackboard()
    parent_session = session_id or f"deleg:{uuid.uuid4().hex[:10]}"
    sub_session_id = f"{parent_session}:sub:{agent_id}:{uuid.uuid4().hex[:6]}"

    blackboard_text: str | None = None
    if use_blackboard and spec.role == "reviewer":
        entries = bb.list_entries(tenant_id, parent_session)
        blackboard_text = format_entries_for_reviewer(entries) or None

    messages = _build_delegation_messages(
        spec=spec,
        task=task,
        inputs=inputs,
        blackboard_text=blackboard_text,
    )
    tool_tuple = resolve_delegation_tools(spec.allowed_tools, allowed_tools)

    try:
        from packages.platform import get_settings
        from packages.agent.registry import ToolRegistry
        from packages.agent.runner import AgentRunError, run_agent
        from packages.agent.session import SessionStore

        settings = get_settings()
        model = spec.model or settings.default_model
        # 子 Agent 使用独立内存 session，避免污染父会话
        sub_store = SessionStore()

        run_result = await asyncio.wait_for(
            run_agent(
                tenant_id=tenant_id,
                session_id=sub_session_id,
                new_messages=messages,
                allowed_tools=tool_tuple,
                allowed_models=allowed_models or (),
                model=model,
                session_store=sub_store,
                registry=ToolRegistry(),
            ),
            timeout=timeout_seconds,
        )
        final_message = str(run_result.get("final_message") or "")
        tool_trace = run_result.get("tool_calls") or []
        platform = run_result.get("_platform") or {}
        usage = platform.get("usage") if isinstance(platform, dict) else {}
        if not isinstance(usage, dict):
            usage = {}

        entry_id: str | None = None
        if use_blackboard and final_message.strip():
            entry = bb.append(
                tenant_id,
                parent_session,
                agent_id=agent_id,
                role=spec.role,
                content=final_message,
                kind="review" if spec.role == "reviewer" else "delegation",
            )
            entry_id = entry.entry_id

        registry.mark_healthy(agent_id)
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status=str(run_result.get("status") or "completed"),
            output=final_message,
            usage=usage,
            execution_time_ms=(time.time() - start_time) * 1000,
            delegation_depth=len(stack) - 1,
            trace=tool_trace if isinstance(tool_trace, list) else [],
            blackboard_entry_id=entry_id,
            sub_session_id=sub_session_id,
        )
    except TimeoutError:
        error_msg = f"委托超时 {timeout_seconds}s"
        registry.mark_error(agent_id, error_msg)
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="timeout",
            output="",
            error=error_msg,
            execution_time_ms=(time.time() - start_time) * 1000,
            delegation_depth=len(stack) - 1,
            sub_session_id=sub_session_id,
        )
    except AgentRunError as e:
        error_msg = f"{e.code}: {e}"
        registry.mark_error(agent_id, error_msg)
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=error_msg,
            execution_time_ms=(time.time() - start_time) * 1000,
            delegation_depth=len(stack) - 1,
            sub_session_id=sub_session_id,
        )
    except Exception as e:
        error_msg = f"委托异常: {type(e).__name__}: {e}"
        registry.mark_error(agent_id, error_msg)
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="failed",
            output="",
            error=error_msg,
            execution_time_ms=(time.time() - start_time) * 1000,
            delegation_depth=len(stack) - 1,
            sub_session_id=sub_session_id,
        )


async def parallel_delegate(
    *,
    delegations: list[dict[str, Any]],
    tenant_id: str = "admin",
    session_id: str | None = None,
    parent_stack: list[str] | None = None,
    timeout_seconds: float = 60.0,
    allowed_tools: tuple[str, ...] | None = None,
    allowed_models: tuple[str, ...] | None = None,
) -> list[DelegationResult]:
    """并行委托多个 Agent。"""
    stack = list(parent_stack or [])
    tasks = [
        delegate_to_agent(
            agent_id=d["agent_id"],
            task=d["task"],
            tenant_id=tenant_id,
            session_id=session_id,
            inputs=d.get("inputs"),
            delegation_stack=stack,
            timeout_seconds=timeout_seconds,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
        )
        for d in delegations
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[DelegationResult] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            out.append(
                DelegationResult(
                    agent_id=delegations[i].get("agent_id", "unknown"),
                    task=delegations[i].get("task", ""),
                    status="failed",
                    output="",
                    error=f"PARALLEL_ERROR: {r}",
                )
            )
        else:
            out.append(r)
    return out
