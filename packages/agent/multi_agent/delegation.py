"""Agent 委托逻辑 — 主 Agent 调用子 Agent 执行任务。

委托流程：
    1. 主 Agent 通过 agent_call 节点指定 sub_agent_id + task
    2. 子 Agent 用自己的 system_prompt + 工具集 + 模型执行 task
    3. 返回结果给主 Agent（写入 ExecutionContext.outputs[node_id]）

防递归：
    - 委托深度限制（max_delegation_depth）
    - 委托栈跟踪（防止 A→B→A 循环）
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

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


async def delegate_to_agent(
    *,
    agent_id: str,
    task: str,
    inputs: dict[str, Any] | None = None,
    delegation_stack: list[str] | None = None,
    max_depth: int | None = None,
    timeout_seconds: float = 60.0,
) -> DelegationResult:
    """委托任务给指定 Agent。

    Args:
        agent_id: 目标 Agent ID
        task: 任务描述（user message）
        inputs: 额外输入变量
        delegation_stack: 委托栈（防递归）
        max_depth: 最大委托深度（None 则用 AgentSpec 配置）
        timeout_seconds: 超时

    Returns:
        DelegationResult
    """
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
    # 防递归
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

    # 构造子 Agent 的消息：system_prompt + user task
    messages: list[dict[str, Any]] = []
    if spec.system_prompt:
        messages.append({"role": "system", "content": spec.system_prompt})
    # 注入额外输入作为上下文
    context_parts: list[str] = []
    if inputs:
        for k, v in inputs.items():
            context_parts.append(f"{k}: {v}")
    user_content = task
    if context_parts:
        user_content = "\n".join(context_parts) + "\n\n任务：\n" + task
    messages.append({"role": "user", "content": user_content})

    # 调用 LLM
    try:
        from apps.gateway.model_router import forward_with_model_router
        from apps.gateway.settings import get_settings

        settings = get_settings()
        model = spec.model or settings.default_model
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
        }
        routed = await asyncio.wait_for(
            forward_with_model_router(payload, requested_model=model),
            timeout=timeout_seconds,
        )
        if routed.body is None or not (200 <= routed.status < 300):
            error_msg = f"LLM 调用失败 status={routed.status} error={routed.error}"
            registry.mark_error(agent_id, error_msg)
            return DelegationResult(
                agent_id=agent_id,
                task=task,
                status="failed",
                output="",
                error=error_msg,
                execution_time_ms=(time.time() - start_time) * 1000,
                delegation_depth=len(stack) - 1,
            )
        choices = routed.body.get("choices") or []
        if not choices:
            return DelegationResult(
                agent_id=agent_id,
                task=task,
                status="completed",
                output="",
                usage=routed.body.get("usage", {}),
                execution_time_ms=(time.time() - start_time) * 1000,
                delegation_depth=len(stack) - 1,
            )
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        registry.mark_healthy(agent_id)
        return DelegationResult(
            agent_id=agent_id,
            task=task,
            status="completed",
            output=content,
            usage=routed.body.get("usage", {}),
            execution_time_ms=(time.time() - start_time) * 1000,
            delegation_depth=len(stack) - 1,
            trace=[
                {
                    "agent_id": agent_id,
                    "model": routed.model_used or model,
                    "messages_count": len(messages),
                }
            ],
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
        )


async def parallel_delegate(
    *,
    delegations: list[dict[str, Any]],
    parent_stack: list[str] | None = None,
    timeout_seconds: float = 60.0,
) -> list[DelegationResult]:
    """并行委托多个 Agent。

    Args:
        delegations: [{"agent_id": str, "task": str, "inputs": dict}, ...]
        parent_stack: 父委托栈
        timeout_seconds: 每个委托的超时

    Returns:
        list[DelegationResult]，顺序与输入一致
    """
    stack = list(parent_stack or [])
    tasks = [
        delegate_to_agent(
            agent_id=d["agent_id"],
            task=d["task"],
            inputs=d.get("inputs"),
            delegation_stack=stack,
            timeout_seconds=timeout_seconds,
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
