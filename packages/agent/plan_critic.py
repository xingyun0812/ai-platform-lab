"""packages/agent/plan_critic.py — Phase Q Q3 Replan on failure Critic."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("ai_platform.agent.plan_critic")

_FALLBACK_CRITIC_TEMPLATE = """\
原始 Plan（JSON）：
{plan_json}

失败的 step（id={failed_step_id}）：{failed_step_desc}
失败原因：{failure_reason}
{context_block}请输出修订后的完整 Plan（JSON），修复失败 step 或替换为可执行的步骤。
只输出合法 JSON，不要其他文字。"""


def build_critic_prompt(
    *,
    plan: Any,  # AgentPlan  (avoid circular import at module-level)
    failed_step: Any,  # PlanStep
    failure_reason: str,
    context: str | None = None,
) -> str:
    """构造 critic LLM 的 user prompt。"""
    plan_dict: dict[str, Any] = {
        "goal": plan.goal,
        "steps": [
            {
                "id": s.id,
                "description": s.description,
                "tool_hint": s.tool_hint,
                "agent_hint": s.agent_hint,
                "depends_on": s.depends_on,
            }
            for s in plan.steps
        ],
    }
    plan_json = json.dumps(plan_dict, ensure_ascii=False, indent=2)
    context_block = f"背景信息：\n{context.strip()}\n" if context and context.strip() else ""

    # Try prompt registry first (graceful degradation if unavailable)
    try:
        from apps.gateway.settings import get_settings

        settings = get_settings()
        if settings.prompt_registry_enabled:
            from packages.prompt import get_registry

            reg = get_registry()
            if reg is not None:
                entry = reg.get_active("agent_plan_critic")
                if entry is not None and entry.version > 0:
                    return entry.render(
                        {
                            "plan_json": plan_json,
                            "failed_step_id": failed_step.id,
                            "failed_step_desc": failed_step.description,
                            "failure_reason": failure_reason,
                            "context_block": context_block,
                        }
                    )
    except Exception as exc:
        logger.debug("agent_plan_critic prompt registry lookup failed: %s", exc)

    return _FALLBACK_CRITIC_TEMPLATE.format(
        plan_json=plan_json,
        failed_step_id=failed_step.id,
        failed_step_desc=failed_step.description,
        failure_reason=failure_reason,
        context_block=context_block,
    )


def _extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract first JSON object from LLM output; return None on failure."""
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def _check_model_allowed(
    model: str,
    allowed_models: tuple[str, ...],
) -> tuple[bool, str]:
    """Thin wrapper around is_model_allowed; isolated for unit testing."""
    from apps.gateway.model_router import is_model_allowed

    return is_model_allowed(model, tenant_default=None, allowed_models=allowed_models)


async def _call_upstream(
    model: str,
    user_prompt: str,
) -> str | None:
    """调用 upstream LLM 并返回 content 字符串；失败返回 None（降级）。

    独立为函数以便单测 patch。
    """
    from apps.gateway.model_router import forward_with_model_router

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是任务规划修订助手，只输出合法 JSON，不要其他文字。",
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    try:
        route = await forward_with_model_router(payload)
        if route.status != 200 or not route.body:
            logger.warning(
                "_call_upstream: upstream error status=%d, error=%s",
                route.status,
                getattr(route, "error", None),
            )
            return None
        choices = route.body.get("choices") or []
        if not choices:
            logger.warning("_call_upstream: empty choices from upstream")
            return None
        content = (choices[0].get("message") or {}).get("content") or ""
        return content
    except Exception as exc:
        logger.warning("_call_upstream: call failed: %s", exc)
        return None


async def replan_after_failure(
    *,
    plan: Any,  # AgentPlan
    failed_step: Any,  # PlanStep
    failure_reason: str,
    context: str | None = None,
    model: str | None = None,
    allowed_models: tuple[str, ...],
    max_replan_attempts: int = 2,
    attempt: int = 0,
) -> Any | None:  # AgentPlan | None
    """调用 LLM Critic 修订 Plan（局部 patch 失败的 step）。

    返回修订后的 AgentPlan，或 None（已达到最大重试次数或 critic 失败）。

    降级：若 critic LLM 调用失败或输出无法解析，返回 None。
    """
    if attempt >= max_replan_attempts:
        logger.info(
            "replan_after_failure: max_replan_attempts=%d reached, skipping critic",
            max_replan_attempts,
        )
        return None

    from apps.gateway.settings import get_settings
    from packages.agent.planner import PlannerError, parse_plan

    settings = get_settings()
    resolved_model_name = model or settings.agent_model or settings.default_model

    allowed, resolved_model = _check_model_allowed(resolved_model_name, allowed_models)
    if not allowed:
        logger.warning(
            "replan_after_failure: model %s not in allowed_models, skipping critic",
            resolved_model_name,
        )
        return None

    user_prompt = build_critic_prompt(
        plan=plan,
        failed_step=failed_step,
        failure_reason=failure_reason,
        context=context,
    )

    content = await _call_upstream(resolved_model, user_prompt)
    if content is None:
        return None

    data = _extract_json_from_text(content)
    if data is None:
        logger.warning(
            "replan_after_failure: failed to parse JSON from critic output (attempt=%d)", attempt
        )
        return None

    try:
        new_plan = parse_plan(data)
        logger.info(
            "replan_after_failure: critic produced revised plan with %d steps (attempt=%d)",
            len(new_plan.steps),
            attempt,
        )
        return new_plan
    except (PlannerError, Exception) as exc:
        logger.warning(
            "replan_after_failure: revised plan validation failed: %s (attempt=%d)", exc, attempt
        )
        return None
