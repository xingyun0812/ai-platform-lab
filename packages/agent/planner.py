from __future__ import annotations

import json
import logging
import os
import re
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from apps.gateway.model_router import forward_with_model_router, is_model_allowed
from apps.gateway.settings import get_settings
from packages.agent.perf_metrics import get_agent_perf_metrics
from packages.agent.registry import ToolRegistry
from packages.contracts.agent_schemas import AgentPlan, PlanStep, ToolCallRecord
from packages.observability.context import get_trace_id

logger = logging.getLogger("ai_platform.agent.planner")

_FALLBACK_PLANNER_TEMPLATE = """你是任务规划助手。根据用户目标输出 **仅 JSON**（不要 markdown 包裹），格式：
{{"goal":"<复述目标>","steps":[{{"id":"s1","description":"...","tool_hint":"calc|get_kb_snippet|null","agent_hint":null,"depends_on":[]}}]}}

规则：
- steps 至少 1 步，id 唯一（s1,s2,...）
- depends_on 引用已存在 step id，不得成环
- tool_hint 仅从可用工具中选：{tools}
- description 用简体中文，可执行、可验证

{context_block}

用户目标：{goal}"""


class PlannerError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


def build_response_format_schema() -> dict[str, Any]:
    """构造 json_schema response_format payload。"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_plan",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "tool_hint": {"type": ["string", "null"]},
                                "agent_hint": {"type": ["string", "null"]},
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["id", "description", "depends_on"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["goal", "steps"],
                "additionalProperties": False,
            },
        },
    }


def is_structured_mode() -> bool:
    """检查是否启用 structured output 模式。"""
    return os.getenv("PLAN_OUTPUT_MODE", "structured").lower() == "structured"


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象（支持 ```json 包裹）。"""
    raw = (text or "").strip()
    if not raw:
        raise PlannerError("PLAN_PARSE_ERROR", "LLM 返回空内容")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlannerError("PLAN_PARSE_ERROR", f"JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise PlannerError("PLAN_PARSE_ERROR", "Plan 根节点必须是 object")
    return data


def parse_plan(data: dict[str, Any]) -> AgentPlan:
    """解析并校验 Plan 结构。"""
    try:
        plan = AgentPlan.model_validate(data)
    except Exception as e:
        raise PlannerError("PLAN_INVALID", str(e)) from e
    validate_plan(plan)
    return plan


def validate_plan(plan: AgentPlan) -> None:
    if not plan.goal.strip():
        raise PlannerError("PLAN_INVALID", "goal 不能为空")
    if not plan.steps:
        raise PlannerError("PLAN_INVALID", "steps 不能为空")

    ids = [s.id for s in plan.steps]
    if len(ids) != len(set(ids)):
        raise PlannerError("PLAN_INVALID", "step id 重复", detail={"ids": ids})

    id_set = set(ids)
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in id_set:
                raise PlannerError(
                    "PLAN_INVALID",
                    f"depends_on 引用未知 step: {dep}",
                    detail={"step_id": step.id, "depends_on": dep},
                )

    if topological_sort(plan.steps) is None:
        raise PlannerError("PLAN_CYCLE", "Plan 存在循环依赖")


def topological_sort(steps: list[PlanStep]) -> list[PlanStep] | None:
    """Kahn 拓扑排序；有环返回 None。"""
    by_id = {s.id: s for s in steps}
    indegree = {s.id: 0 for s in steps}
    graph: dict[str, list[str]] = {s.id: [] for s in steps}
    for step in steps:
        for dep in step.depends_on:
            graph[dep].append(step.id)
            indegree[step.id] += 1

    queue = deque([sid for sid, deg in indegree.items() if deg == 0])
    ordered: list[PlanStep] = []
    while queue:
        sid = queue.popleft()
        ordered.append(by_id[sid])
        for nxt in graph[sid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(ordered) != len(steps):
        return None
    return ordered


def ordered_plan_steps(plan: AgentPlan) -> list[PlanStep]:
    ordered = topological_sort(plan.steps)
    if ordered is None:
        raise PlannerError("PLAN_CYCLE", "Plan 存在循环依赖")
    return ordered


def build_planner_user_prompt(
    *,
    goal: str,
    context: str | None,
    available_tools: tuple[str, ...],
) -> str:
    settings = get_settings()
    tools_str = ", ".join(available_tools) if available_tools else "calc, get_kb_snippet"
    context_block = f"背景信息：\n{context.strip()}\n" if context and context.strip() else ""

    if settings.prompt_registry_enabled:
        from packages.prompt import get_registry

        reg = get_registry()
        if reg is not None:
            try:
                entry = reg.get_active("agent_planner")
                if entry is not None and entry.version > 0:
                    return entry.render(
                        {
                            "goal": goal.strip(),
                            "tools": tools_str,
                            "context_block": context_block,
                        }
                    )
            except Exception as e:
                logger.warning("agent_planner prompt lookup failed: %s", e)

    return _FALLBACK_PLANNER_TEMPLATE.format(
        goal=goal.strip(),
        tools=tools_str,
        context_block=context_block,
    )


def _assistant_content_from_route(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    choices = body.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def _build_base_payload(
    resolved_model: str,
    user_prompt: str,
) -> dict[str, Any]:
    """构建不含 response_format 的基础 payload。"""
    return {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON，不要其它文字。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }


async def _call_upstream(payload: dict[str, Any]) -> str:
    """调用 upstream 并返回 content 字符串；失败抛 PlannerError。"""
    route = await forward_with_model_router(payload)
    if route.status != 200 or not route.body:
        raise PlannerError(
            "PLAN_UPSTREAM_ERROR",
            route.error or f"upstream status={route.status}",
            detail={"status": route.status},
        )
    return _assistant_content_from_route(route.body)


async def generate_plan(
    *,
    goal: str,
    context: str | None = None,
    model: str | None = None,
    allowed_models: tuple[str, ...],
    allowed_tools: tuple[str, ...],
    registry: ToolRegistry | None = None,
) -> tuple[AgentPlan, str]:
    """调用 LLM 生成 Plan，返回 (plan, resolved_model)。

    若 PLAN_OUTPUT_MODE=structured（默认），优先走 response_format json_schema 路径；
    若 upstream 拒绝或解析失败，自动降级到 legacy extract_json_object 路径。
    若 PLAN_OUTPUT_MODE=legacy，直接走 legacy 路径。
    """
    goal = goal.strip()
    if not goal:
        raise PlannerError("PLAN_INVALID", "goal 不能为空")

    settings = get_settings()
    reg = registry or ToolRegistry()
    available = tuple(t.name for t in reg.list_for_tenant(allowed_tools))

    allowed, resolved_model = is_model_allowed(
        model or settings.agent_model,
        tenant_default=None,
        allowed_models=allowed_models,
    )
    if not allowed:
        raise PlannerError(
            "MODEL_NOT_ALLOWED",
            f"模型不在白名单: {model or settings.agent_model}",
            detail={"allowed_models": list(allowed_models)},
        )

    user_prompt = build_planner_user_prompt(
        goal=goal,
        context=context,
        available_tools=available,
    )
    base_payload = _build_base_payload(resolved_model, user_prompt)

    if is_structured_mode():
        # --- Structured path: add response_format ---
        structured_payload = {**base_payload, "response_format": build_response_format_schema()}
        try:
            content = await _call_upstream(structured_payload)
            plan = parse_plan(extract_json_object(content))
            return plan, resolved_model
        except PlannerError as exc:
            # Degrade to legacy path on any planner-level failure
            logger.warning(
                "structured plan path failed (%s: %s), fallback to legacy path",
                exc.code,
                exc.message,
            )
            # Fall through to legacy below

    # --- Legacy path (no response_format) ---
    content = await _call_upstream(base_payload)
    plan = parse_plan(extract_json_object(content))
    return plan, resolved_model


def format_step_user_message(step: PlanStep, *, index: int, total: int) -> str:
    lines = [
        f"[Plan step {index}/{total} · {step.id}] {step.description}",
    ]
    if step.tool_hint:
        lines.append(f"建议工具：{step.tool_hint}")
    if step.agent_hint:
        lines.append(f"建议 Agent：{step.agent_hint}")
    return "\n".join(lines)


async def execute_plan_with_agent(
    *,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str,
    allowed_tools: tuple[str, ...],
    allowed_models: tuple[str, ...],
    model: str | None,
    session_store: Any,
    step_system_messages: list[dict[str, Any]] | None = None,
    run_agent_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """按拓扑顺序逐步调用 run_agent。"""
    from packages.agent.runner import run_agent

    runner = run_agent_fn or run_agent
    steps = ordered_plan_steps(plan)
    total = len(steps)
    all_tool_calls: list[ToolCallRecord] = []
    agent_steps = 0
    final_message = ""
    resolved_model = model or get_settings().agent_model or get_settings().default_model
    last_status = "completed"
    last_approval_id: str | None = None

    for idx, step in enumerate(steps, start=1):
        step_msg = format_step_user_message(step, index=idx, total=total)
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": step_msg}]
        if step_system_messages and idx == 1:
            new_messages = [*step_system_messages, *new_messages]

        result = await runner(
            tenant_id=tenant_id,
            session_id=session_id,
            new_messages=new_messages,
            allowed_tools=allowed_tools,
            allowed_models=allowed_models,
            model=model,
            session_store=session_store,
        )
        resolved_model = result.get("model") or resolved_model
        agent_steps += int(result.get("steps") or 0)
        final_message = str(result.get("final_message") or final_message)
        last_status = str(result.get("status") or last_status)
        last_approval_id = result.get("approval_id") or last_approval_id
        for tc in result.get("tool_calls") or []:
            if isinstance(tc, ToolCallRecord):
                all_tool_calls.append(tc)
            elif isinstance(tc, dict):
                all_tool_calls.append(ToolCallRecord.model_validate(tc))

        if last_status == "pending_approval":
            get_agent_perf_metrics().record_plan_steps(tenant_id=tenant_id, steps=idx - 1)
            return {
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
                "plan_steps_completed": idx - 1,
            }

    get_agent_perf_metrics().record_plan_steps(tenant_id=tenant_id, steps=total)
    return {
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
        "plan_steps_completed": total,
    }
