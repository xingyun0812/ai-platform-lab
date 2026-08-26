"""反思成本/收益指标（reflection_metrics）— token、时延、触发原因、收敛轮数、成本与 ROI。

纯逻辑 + mock store：不依赖 LLM / DB / gateway，可单测。

指标类型（record_reflection_use）：\\n
    reason          — 触发原因（consecutive_failure / distortion / negative_feedback / task_failure）。\\n
    depth           — 生效反思深度（full / light / off / legacy）。\\n
    tokens          — 消耗 token。\\n
    latency_ms      — 时延（毫秒）。\\n
    rounds          — 收敛轮数。\\n
    cost            — 估算成本（默认按 token 计）。\\n
    outcome         — 结果（check / pass / skip / fail_open / escalated / converged）。\\n
    deduped         — 是否去重跳过。\\n
    escalated       — 是否升级大模型。\\n
\\n
ROI 聚合（compute_roi）：将反思总成本与下游错误率/返工信号联动，估算成本收益。\\n
    cost_total          — 反思累计成本。\\n
    benefit              = 下游错误率下降量 × 单次错误成本。\\n
    rework_saved         = 返工减少量 × 单次返工成本。\\n
    roi                  = (benefit - cost_total) / cost_total。\\n
\\n
ROI 指标是「可观测」而非自动决策：仅输出指标供运维按 ROI 调深度。\\n
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: 每千 token 的估算成本（美元）。可配置/可覆盖。可 mock 注入。
DEFAULT_COST_PER_1K_TOKENS = 0.0002


@dataclass
class ReflectionUse:
    """一次反思使用的成本记录。"""

    reason: str
    depth: str
    action: str  # check | pass | skip
    tokens: int = 0
    latency_ms: float = 0.0
    rounds: int = 0
    cost: float = 0.0
    outcome: str = "unknown"
    deduped: bool = False
    escalated: bool = False
    triggered_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "depth": self.depth,
            "action": self.action,
            "tokens": self.tokens,
            "latency_ms": round(self.latency_ms, 2),
            "rounds": self.rounds,
            "cost": round(self.cost, 6),
            "outcome": self.outcome,
            "deduped": self.deduped,
            "escalated": self.escalated,
            "triggered_at": self.triggered_at,
        }


class ReflectionMetricsStore:
    """线程安全的内存反思指标存储（可 mock 注入）。"""

    def __init__(self, cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS) -> None:
        self._lock = threading.RLock()
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self._uses: list[ReflectionUse] = []

    # -- 写入 --------------------------------------------------------------

    def record(self, use: ReflectionUse | None = None, **kwargs: Any) -> ReflectionUse:
        """记录一次反思使用。接受 ReflectionUse 或 kwargs。返回构建好的记录。"""
        if use is None:
            use = ReflectionUse(**kwargs)  # type: ignore[arg-type]
        with self._lock:
            self._uses.append(use)
        return use

    # -- 读取 --------------------------------------------------------------

    def all(self) -> list[ReflectionUse]:
        with self._lock:
            return list(self._uses)

    def count(self) -> int:
        with self._lock:
            return len(self._uses)

    # -- 聚合 --------------------------------------------------------------

    def total_tokens(self) -> int:
        with self._lock:
            return sum(u.tokens for u in self._uses)

    def total_cost(self) -> float:
        with self._lock:
            return sum(u.cost for u in self._uses)

    def total_latency_ms(self) -> float:
        with self._lock:
            return sum(u.latency_ms for u in self._uses)

    def by_depth(self) -> dict[str, dict[str, Any]]:
        """按深度聚合：触发次数、token、成本、时延、平均收敛轮数。"""
        with self._lock:
            groups: defaultdict[str, list[ReflectionUse]] = defaultdict(list)
            for u in self._uses:
                groups[u.depth].append(u)
        out: dict[str, dict[str, Any]] = {}
        for depth, uses in groups.items():
            out[depth] = {
                "uses": len(uses),
                "tokens": sum(u.tokens for u in uses),
                "cost": round(sum(u.cost for u in uses), 6),
                "latency_ms": round(sum(u.latency_ms for u in uses), 2),
                "avg_rounds": round((sum(u.rounds for u in uses) / len(uses)) if uses else 0.0, 2),
                "escalated": sum(1 for u in uses if u.escalated),
                "deduped": sum(1 for u in uses if u.deduped),
            }
        return out

    def by_reason(self) -> dict[str, int]:
        """按触发原因统计次数。"""
        with self._lock:
            groups: defaultdict[str, int] = defaultdict(int)
            for u in self._uses:
                groups[u.reason] += 1
        return dict(groups)

    def summary(self) -> dict[str, Any]:
        """总体摘要。"""
        depth = self.by_depth()
        return {
            "total_uses": self.count(),
            "total_tokens": self.total_tokens(),
            "total_cost": round(self.total_cost(), 6),
            "total_latency_ms": round(self.total_latency_ms(), 2),
            "by_depth": depth,
            "by_reason": self.by_reason(),
        }


def record_reflection_use(
    store: ReflectionMetricsStore,
    *,
    reason: str,
    depth: str,
    action: str,
    tokens: int = 0,
    latency_ms: float = 0.0,
    rounds: int = 0,
    outcome: str | None = None,
    deduped: bool = False,
    escalated: bool = False,
) -> ReflectionUse:
    """记录一次反思使用（自动估算成本）。"""
    cost = (float(tokens) / 1000.0) * store.cost_per_1k_tokens
    if outcome is None:
        if deduped:
            outcome = "deduped"
        elif action == "pass":
            outcome = "pass"
        elif escalated:
            outcome = "escalated"
        else:
            outcome = "check"
    return store.record(
        ReflectionUse(
            reason=reason,
            depth=depth,
            action=action,
            tokens=tokens,
            latency_ms=latency_ms,
            rounds=rounds,
            cost=cost,
            outcome=outcome,
            deduped=deduped,
            escalated=escalated,
        )
    )


# ---------------------------------------------------------------------------
# ROI 聚合
# ---------------------------------------------------------------------------


@dataclass
class RoiResult:
    """反思成本收益（ROI）评估结果。"""

    cost_total: float
    benefit: float
    rework_saved: float
    roi_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_total": round(self.cost_total, 6),
            "benefit": round(self.benefit, 4),
            "rework_saved": round(self.rework_saved, 4),
            "roi_ratio": round(self.roi_ratio, 2),
            "roi_pct": round(self.roi_ratio * 100.0, 1),
        }


def compute_roi(
    store: ReflectionMetricsStore,
    *,
    error_rate_before: float,
    error_rate_after: float,
    cost_per_error: float,
    rework_count_before: int,
    rework_count_after: int,
    cost_per_rework: float,
) -> RoiResult:
    """评估反思成本收益。

    Args:
        store: 反思指标存储（成本来源）。
        error_rate_before/after: 接入反思前后的下游错误率。
        cost_per_error: 单次错误成本。
        rework_count_before/after: 接入前后的返工次数。
        cost_per_rework: 单次返工成本。

    Returns:
        RoiResult。roi_ratio = (benefit - cost_total) / cost_total。
        当 cost_total 为 0（反思未消耗成本）时 roi_ratio 取极大（无成本即纯收益）。
    """
    benefit = (error_rate_before - error_rate_after) * cost_per_error
    rework_saved = (rework_count_before - rework_count_after) * cost_per_rework
    cost_total = store.total_cost()
    total_benefit = benefit + rework_saved
    if cost_total <= 0:
        roi_ratio = float("inf") if total_benefit > 0 else 0.0
    else:
        roi_ratio = (total_benefit - cost_total) / cost_total
    return RoiResult(
        cost_total=cost_total,
        benefit=benefit,
        rework_saved=rework_saved,
        roi_ratio=roi_ratio,
    )
