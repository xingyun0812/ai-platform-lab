"""Agent run 生命周期 hooks — plan/run 完成边界触发自进化等后台任务。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger("ai_platform.agent.run_lifecycle")

_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_SKIP_SELF_EVOLVE_STATUSES = frozenset(
    {
        "pending_approval",
        "pending_plan_approval",
        "paused",
        "running",
    }
)


def self_evolve_enabled() -> bool:
    """是否启用 run 结束自进化（默认开，SELF_EVOLVE_ENABLED=false 关闭）。"""
    return os.environ.get("SELF_EVOLVE_ENABLED", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def is_terminal_run_status(status: str | None) -> bool:
    normalized = str(status or "").strip().lower()
    if not normalized or normalized in _SKIP_SELF_EVOLVE_STATUSES:
        return False
    return normalized in _TERMINAL_STATUSES


def outcome_from_run_status(status: str | None) -> str:
    normalized = str(status or "completed").strip().lower()
    if normalized == "completed":
        return "success"
    return normalized or "unknown"


def extract_tool_calls_for_self_evolve(tool_calls: Any) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    out: list[dict[str, Any]] = []
    for item in tool_calls:
        if isinstance(item, dict):
            out.append(item)
            continue
        to_dict = getattr(item, "to_dict", None)
        if callable(to_dict):
            out.append(to_dict())
            continue
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            out.append(model_dump())
            continue
        out.append({"value": str(item)})
    return out


def schedule_self_evolve_after_run(
    *,
    result: dict[str, Any],
    tenant_id: str,
    model: str | None = None,
    plan: Any | None = None,
) -> None:
    """fire-and-forget：在 run/plan 到达终态时调度 trigger_self_evolve。"""
    if not self_evolve_enabled():
        return

    status = str(result.get("status", ""))
    if not is_terminal_run_status(status):
        return

    resolved_plan = plan if plan is not None else result.get("plan")
    outcome = outcome_from_run_status(status)
    tool_calls = extract_tool_calls_for_self_evolve(result.get("tool_calls"))

    async def _run() -> None:
        try:
            from packages.agent.self_evolve import trigger_self_evolve

            await trigger_self_evolve(
                resolved_plan,
                outcome,
                tenant_id=tenant_id,
                tool_calls=tool_calls,
                model=model or result.get("model"),
            )
        except Exception as exc:  # noqa: BLE001 — 后台任务，不影响主路径
            logger.warning("background trigger_self_evolve failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("no running event loop; skip self_evolve schedule tenant=%s", tenant_id)
        return

    loop.create_task(_run())


def finalize_agent_run_result(
    result: dict[str, Any],
    *,
    tenant_id: str,
    model: str | None = None,
    plan: Any | None = None,
) -> dict[str, Any]:
    """返回原 result，并在终态时调度 self_evolve（graph_runtime 等入口使用）。"""
    schedule_self_evolve_after_run(
        result=result,
        tenant_id=tenant_id,
        model=model,
        plan=plan,
    )
    return result
