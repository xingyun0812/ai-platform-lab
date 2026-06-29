"""packages/agent/self_evolve.py — Phase R R1 Agent 自进化主循环。

核心流程：
  reflect_on_run(plan, outcome) → LLM 生成 lessons
  maybe_patch_strategy(lessons, current_strategy) → 生成策略 patch（HITL 审批）
  trigger_self_evolve(plan, outcome, ...) → 串联全流程（异步，不阻塞主流程）
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.agent.self_evolve")

# 每日策略 patch 上限（可通过环境变量覆盖）
_DEFAULT_MAX_PATCHES_PER_DAY = 5


# ---------------------------------------------------------------------------
# StrategyPatch dataclass
# ---------------------------------------------------------------------------


@dataclass
class StrategyPatch:
    """LLM 基于 lessons 提出的策略修改建议。"""

    patch_id: str
    tenant_id: str
    lessons: str
    proposed_change: dict[str, Any]  # {field, old, new}
    status: str = "pending"  # pending | approved | rejected
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "tenant_id": self.tenant_id,
            "lessons": self.lessons,
            "proposed_change": self.proposed_change,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
        }


# ---------------------------------------------------------------------------
# StrategyPatchStore
# ---------------------------------------------------------------------------


class StrategyPatchStore:
    """线程安全的策略 patch 内存存储。"""

    def __init__(self, max_patches_per_day: int = _DEFAULT_MAX_PATCHES_PER_DAY) -> None:
        self._lock = threading.RLock()
        self._patches: dict[str, StrategyPatch] = {}
        self.max_patches_per_day = max_patches_per_day

    def add(self, patch: StrategyPatch) -> StrategyPatch:
        with self._lock:
            self._patches[patch.patch_id] = patch
        return patch

    def get(self, patch_id: str) -> StrategyPatch | None:
        with self._lock:
            return self._patches.get(patch_id)

    def list_all(self) -> list[StrategyPatch]:
        with self._lock:
            return list(self._patches.values())

    def count_today(self, tenant_id: str) -> int:
        """统计今天（UTC 日期）该 tenant 已生成的 patch 数量。"""
        today_start = _today_start_ts()
        with self._lock:
            return sum(
                1
                for p in self._patches.values()
                if p.tenant_id == tenant_id and p.created_at >= today_start
            )

    def approve(self, patch_id: str, decided_by: str = "system") -> bool:
        with self._lock:
            patch = self._patches.get(patch_id)
            if patch is None:
                return False
            patch.status = "approved"
            patch.decided_at = time.time()
            patch.decided_by = decided_by
            return True

    def reject(self, patch_id: str, decided_by: str = "system") -> bool:
        with self._lock:
            patch = self._patches.get(patch_id)
            if patch is None:
                return False
            patch.status = "rejected"
            patch.decided_at = time.time()
            patch.decided_by = decided_by
            return True


def _today_start_ts() -> float:
    """返回今日 00:00:00 UTC 的 Unix 时间戳。"""
    import datetime

    now = datetime.datetime.utcnow()
    today = datetime.datetime(now.year, now.month, now.day)
    return today.timestamp()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_patch_store: StrategyPatchStore | None = None
_patch_store_lock = threading.Lock()


def get_strategy_patch_store() -> StrategyPatchStore:
    global _patch_store
    if _patch_store is None:
        with _patch_store_lock:
            if _patch_store is None:
                _patch_store = StrategyPatchStore()
    return _patch_store


def reset_strategy_patch_store_for_tests() -> None:
    global _patch_store
    with _patch_store_lock:
        _patch_store = None


# ---------------------------------------------------------------------------
# Core async functions
# ---------------------------------------------------------------------------

_REFLECT_PROMPT_TEMPLATE = """你是 Agent 反思助手。请分析这次任务执行情况，提炼 3-5 条可复用的经验教训。

任务目标：{goal}
执行结果：{outcome}
Plan 摘要：{plan_summary}
关键 Tool Calls（摘要）：{tool_calls_summary}

请输出格式：
经验 1: <内容>
经验 2: <内容>
...

只输出经验条目，不要其他解释。"""

_STRATEGY_PATCH_PROMPT_TEMPLATE = """你是 Agent 策略优化助手。根据以下经验教训，提出一条具体的策略改进建议。

经验教训：
{lessons}

当前策略配置：
{current_strategy}

请输出 JSON 格式的策略改进建议（仅 JSON）：
{{"field": "plan_prompt"|"tool_selection"|"reasoning_mode", "old": "<当前值>", "new": "<建议值>", "reason": "<原因>"}}"""


async def reflect_on_run(
    plan: Any,
    outcome: str,
    tool_calls: list[dict[str, Any]] | None = None,
    *,
    tenant_id: str = "default",
    model: str | None = None,
) -> str:
    """调用 LLM 对本次 run 进行反思，返回 lessons 文本。

    失败时回退到简单模板，不阻塞主流程。
    """
    tool_calls = tool_calls or []
    try:
        from packages.platform import forward_with_model_router, get_settings

        settings = get_settings()
        resolved_model = model or settings.agent_model

        # 构建 plan 摘要
        goal = getattr(plan, "goal", str(plan))
        steps = getattr(plan, "steps", [])
        plan_summary = f"goal={goal}, steps={[getattr(s, 'description', str(s)) for s in steps]}"

        # tool_calls 摘要（只取前 5 条）
        tc_summary = str(tool_calls[:5]) if tool_calls else "无"

        user_prompt = _REFLECT_PROMPT_TEMPLATE.format(
            goal=goal,
            outcome=outcome,
            plan_summary=plan_summary,
            tool_calls_summary=tc_summary,
        )

        payload = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": "你是 Agent 反思助手，只输出经验条目。"},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
        }

        route = await forward_with_model_router(payload)
        if route.status == 200 and route.body:
            choices = route.body.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content and content.strip():
                    return content.strip()

    except Exception as exc:
        logger.warning("reflect_on_run LLM call failed: %s", exc)

    # 回退：生成简单 lessons
    goal_str = getattr(plan, "goal", str(plan)) if plan else "unknown"
    return (
        f"经验 1: 任务「{goal_str}」已完成，结果={outcome}\n"
        f"经验 2: 关注 tool_calls 数量（本次 {len(tool_calls or [])} 次）\n"
        f"经验 3: 确保每个 step 有明确的验收标准"
    )


async def maybe_patch_strategy(
    lessons: str,
    current_strategy: dict[str, Any] | None = None,
    *,
    tenant_id: str = "default",
    model: str | None = None,
) -> StrategyPatch | None:
    """分析 lessons，提出策略改进建议（StrategyPatch）。

    不直接应用，入队等待 HITL 审批。
    每天最多 max_patches_per_day 次。
    """
    if not lessons or not lessons.strip():
        return None

    patch_store = get_strategy_patch_store()

    # 检查每日上限
    if patch_store.count_today(tenant_id) >= patch_store.max_patches_per_day:
        logger.info("strategy patch daily limit reached for tenant=%s, skipping", tenant_id)
        return None

    current_strategy = current_strategy or {}
    proposed_change: dict[str, Any] = {}

    try:
        from packages.platform import forward_with_model_router, get_settings

        settings = get_settings()
        resolved_model = model or settings.agent_model

        strategy_str = str(current_strategy) if current_strategy else "{}"
        user_prompt = _STRATEGY_PATCH_PROMPT_TEMPLATE.format(
            lessons=lessons,
            current_strategy=strategy_str,
        )

        payload = {
            "model": resolved_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是策略优化助手，只输出合法 JSON，不要其他文字。",
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        route = await forward_with_model_router(payload)
        if route.status == 200 and route.body:
            choices = route.body.get("choices") or []
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                if content and content.strip():
                    import json
                    import re

                    raw = content.strip()
                    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
                    if fence:
                        raw = fence.group(1).strip()
                    try:
                        proposed_change = json.loads(raw)
                    except Exception:
                        proposed_change = {"raw": content}

    except Exception as exc:
        logger.warning("maybe_patch_strategy LLM call failed: %s", exc)
        proposed_change = {"error": str(exc), "lessons_hash": lessons[:50]}

    if not proposed_change:
        return None

    patch = StrategyPatch(
        patch_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        lessons=lessons,
        proposed_change=proposed_change,
        status="pending",
        created_at=time.time(),
    )
    patch_store.add(patch)
    logger.info("strategy patch created patch_id=%s tenant=%s", patch.patch_id, tenant_id)
    return patch


async def trigger_self_evolve(
    plan: Any,
    outcome: str,
    *,
    tenant_id: str = "default",
    tool_calls: list[dict[str, Any]] | None = None,
    current_strategy: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """串联全流程：store_experience + reflect_on_run + maybe_patch_strategy。

    异常隔离：任何步骤失败不影响主流程，返回执行摘要。
    """
    result: dict[str, Any] = {
        "experience_id": None,
        "lessons": None,
        "patch_id": None,
        "errors": [],
    }
    tool_calls = tool_calls or []

    # Step 1: 存储经验
    try:
        from packages.agent.experience_store import (
            build_experience_record,
            store_experience,
        )

        goal = getattr(plan, "goal", str(plan)) if plan else "unknown"
        record = build_experience_record(
            tenant_id=tenant_id,
            goal=goal,
            plan=plan,
            tool_calls=tool_calls,
            outcome=outcome,
            lessons="",  # lessons 稍后填充
        )
        stored = await store_experience(record)
        result["experience_id"] = stored.experience_id

        # Step 2: 反思生成 lessons
        try:
            lessons = await reflect_on_run(
                plan,
                outcome,
                tool_calls,
                tenant_id=tenant_id,
                model=model,
            )
            result["lessons"] = lessons

            # 更新经验记录的 lessons（内存对象，再 store 一次持久化）
            stored.lessons = lessons
            try:
                await store_experience(stored)
            except Exception as exc:
                logger.warning("re-store experience with lessons failed: %s", exc)

            # 记录指标
            try:
                from packages.agent.perf_metrics import get_agent_perf_metrics

                get_agent_perf_metrics().record_self_evolve_experience(tenant_id)
            except Exception as exc:
                logger.warning("perf_metrics record failed: %s", exc)

        except Exception as exc:
            logger.warning("reflect_on_run failed in trigger_self_evolve: %s", exc)
            result["errors"].append(f"reflect: {exc}")
            lessons = ""

        # Step 3: 策略 patch
        if lessons:
            try:
                patch = await maybe_patch_strategy(
                    lessons,
                    current_strategy,
                    tenant_id=tenant_id,
                    model=model,
                )
                if patch:
                    result["patch_id"] = patch.patch_id
                    # 记录指标
                    try:
                        from packages.agent.perf_metrics import get_agent_perf_metrics

                        get_agent_perf_metrics().record_self_evolve_strategy_patch(tenant_id)
                    except Exception as exc:
                        logger.warning("perf_metrics patch record failed: %s", exc)
            except Exception as exc:
                logger.warning("maybe_patch_strategy failed in trigger_self_evolve: %s", exc)
                result["errors"].append(f"patch: {exc}")

    except Exception as exc:
        logger.warning("store_experience failed in trigger_self_evolve: %s", exc)
        result["errors"].append(f"store: {exc}")

    return result


# ---------------------------------------------------------------------------
# HITL approve / reject helpers
# ---------------------------------------------------------------------------


def approve_strategy_patch(patch_id: str, decided_by: str = "human") -> bool:
    """审批通过一个策略 patch（HITL）。仅入库，不直接改 planner.py。"""
    return get_strategy_patch_store().approve(patch_id, decided_by=decided_by)


def reject_strategy_patch(patch_id: str, decided_by: str = "human") -> bool:
    """拒绝一个策略 patch（HITL）。"""
    return get_strategy_patch_store().reject(patch_id, decided_by=decided_by)
