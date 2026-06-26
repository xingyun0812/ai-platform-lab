"""packages/agent/capability_profile.py — Phase R R3 模型能力画像。

存储 ModelCapabilityProfile，供 Model Router 查询。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ai_platform.agent.capability_profile")

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

# 维度名到字段名的映射
_DIM_TO_FIELD: dict[str, str] = {
    "context": "context_mgmt",
    "context_mgmt": "context_mgmt",
    "memory": "long_memory",
    "long_memory": "long_memory",
    "tool": "tool_use",
    "tool_use": "tool_use",
    "planning": "planning",
}

_FIELD_TO_LABEL: dict[str, str] = {
    "context_mgmt": "context",
    "long_memory": "memory",
    "tool_use": "tool",
    "planning": "planning",
}


@dataclass
class CapabilityScoresRef:
    """引用 eval.harness_capability_benchmark.CapabilityScores 的本地镜像，
    避免循环导入；两者字段完全一致。"""

    context_mgmt: float
    long_memory: float
    tool_use: float
    planning: float

    def to_dict(self) -> dict[str, float]:
        return {
            "context_mgmt": self.context_mgmt,
            "long_memory": self.long_memory,
            "tool_use": self.tool_use,
            "planning": self.planning,
        }

    def overall(self) -> float:
        return (self.context_mgmt + self.long_memory + self.tool_use + self.planning) / 4

    def _as_items(self) -> list[tuple[str, float]]:
        return [
            ("context_mgmt", self.context_mgmt),
            ("long_memory", self.long_memory),
            ("tool_use", self.tool_use),
            ("planning", self.planning),
        ]


@dataclass
class ModelCapabilityProfile:
    """单个模型的能力画像快照。"""

    profile_id: str
    model_id: str
    scores: CapabilityScoresRef
    timestamp: float
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "model_id": self.model_id,
            "scores": self.scores.to_dict(),
            "overall": self.scores.overall(),
            "strength": self.strength_dimension(),
            "weakness": self.weakness_dimension(),
            "timestamp": self.timestamp,
            "notes": self.notes,
        }

    def strength_dimension(self) -> str:
        """返回最强维度的短标签（context / memory / tool / planning）。"""
        items = self.scores._as_items()
        best_field = max(items, key=lambda x: x[1])[0]
        return _FIELD_TO_LABEL.get(best_field, best_field)

    def weakness_dimension(self) -> str:
        """返回最弱维度的短标签。"""
        items = self.scores._as_items()
        worst_field = min(items, key=lambda x: x[1])[0]
        return _FIELD_TO_LABEL.get(worst_field, worst_field)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class CapabilityProfileStore:
    """线程安全的 profile 存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, list[ModelCapabilityProfile]] = {}  # model_id -> [profiles]

    def store(self, profile: ModelCapabilityProfile) -> None:
        """存储 profile；同一模型按时间戳追加，保留最新 10 条。"""
        with self._lock:
            lst = self._store.setdefault(profile.model_id, [])
            lst.append(profile)
            # 按时间戳排序，只保留最新 10 条
            lst.sort(key=lambda p: p.timestamp)
            if len(lst) > 10:
                self._store[profile.model_id] = lst[-10:]

    def get_latest(self, model_id: str) -> ModelCapabilityProfile | None:
        """获取指定模型最新的 profile；无记录返回 None。"""
        with self._lock:
            lst = self._store.get(model_id)
            if not lst:
                return None
            return lst[-1]  # 已按时间戳排序，最后一个最新

    def list_all(self) -> list[ModelCapabilityProfile]:
        """返回所有模型的最新 profile 列表。"""
        with self._lock:
            result = []
            for lst in self._store.values():
                if lst:
                    result.append(lst[-1])
            return sorted(result, key=lambda p: p.timestamp, reverse=True)

    def compare(self, m1: str, m2: str) -> dict[str, Any]:
        """返回两个模型最新 profile 的对比 dict。"""
        p1 = self.get_latest(m1)
        p2 = self.get_latest(m2)
        result: dict[str, Any] = {
            "model_1": m1,
            "model_2": m2,
            "found_1": p1 is not None,
            "found_2": p2 is not None,
        }
        if p1 is None or p2 is None:
            result["comparison"] = "insufficient_data"
            return result

        dims = ["context_mgmt", "long_memory", "tool_use", "planning"]
        comparison: dict[str, Any] = {}
        for dim in dims:
            s1 = getattr(p1.scores, dim)
            s2 = getattr(p2.scores, dim)
            diff = s1 - s2
            winner = m1 if diff > 0.001 else (m2 if diff < -0.001 else "tie")
            comparison[dim] = {
                m1: round(s1, 4),
                m2: round(s2, 4),
                "diff": round(diff, 4),
                "winner": winner,
            }

        overall_1 = p1.scores.overall()
        overall_2 = p2.scores.overall()
        result["comparison"] = comparison
        result["overall"] = {
            m1: round(overall_1, 4),
            m2: round(overall_2, 4),
            "winner": m1
            if overall_1 > overall_2 + 0.001
            else (m2 if overall_2 > overall_1 + 0.001 else "tie"),
        }
        return result

    def stats(self) -> dict[str, Any]:
        """返回 store 统计信息。"""
        with self._lock:
            return {
                "total_models": len(self._store),
                "total_profiles": sum(len(lst) for lst in self._store.values()),
            }


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_store_lock = threading.Lock()
_store_instance: CapabilityProfileStore | None = None


def get_capability_profile_store() -> CapabilityProfileStore:
    """获取全局单例 CapabilityProfileStore。"""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = CapabilityProfileStore()
    return _store_instance


def reset_capability_profile_store_for_tests() -> None:
    """测试用：重置全局单例。"""
    global _store_instance
    with _store_lock:
        _store_instance = None


# ---------------------------------------------------------------------------
# Profile ID 生成
# ---------------------------------------------------------------------------


def new_profile_id() -> str:
    return f"prof_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 辅助：维度名 -> scores 字段名
# ---------------------------------------------------------------------------


def dim_to_field(dimension: str) -> str | None:
    """将维度短名（context/memory/tool/planning）转为 scores 字段名。"""
    return _DIM_TO_FIELD.get(dimension.lower())


# ---------------------------------------------------------------------------
# 主入口：跑 benchmark → 入库 → 返回 profile
# ---------------------------------------------------------------------------


async def run_capability_profile(model_id: str, mock: bool = False) -> ModelCapabilityProfile:
    """跑全部 4 维度 benchmark → 入库 → 返回 profile。"""
    from eval.harness_capability_benchmark import run_all_benchmarks

    raw_scores = await run_all_benchmarks(model_id, mock=mock)

    # 将 eval 模块的 CapabilityScores 转换为本地 CapabilityScoresRef
    scores = CapabilityScoresRef(
        context_mgmt=raw_scores.context_mgmt,
        long_memory=raw_scores.long_memory,
        tool_use=raw_scores.tool_use,
        planning=raw_scores.planning,
    )

    profile = ModelCapabilityProfile(
        profile_id=new_profile_id(),
        model_id=model_id,
        scores=scores,
        timestamp=time.time(),
    )
    get_capability_profile_store().store(profile)
    logger.info(
        "Capability profile stored model=%s profile_id=%s overall=%.3f",
        model_id,
        profile.profile_id,
        scores.overall(),
    )
    return profile
