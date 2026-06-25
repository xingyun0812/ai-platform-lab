"""packages/agent/experience_store.py — Phase R R1 Agent 经验库。

Agent 执行完任务后沉淀成功路径为「经验」，下次相似任务复用。
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import AgentPlan


@dataclass
class ExperienceRecord:
    """一条 Agent 执行经验记录。"""

    experience_id: str
    tenant_id: str
    task_signature: str  # 任务签名（goal 的 SHA1 前 16 字符）
    goal: str
    plan: AgentPlan
    tool_calls: list[dict[str, Any]]
    outcome: str  # "success" | "partial" | "failed"
    lessons: str  # LLM 反思生成的 lessons
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "tenant_id": self.tenant_id,
            "task_signature": self.task_signature,
            "goal": self.goal,
            "plan": self.plan.model_dump(),
            "tool_calls": self.tool_calls,
            "outcome": self.outcome,
            "lessons": self.lessons,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class ExperienceStore:
    """线程安全的内存经验库。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, ExperienceRecord] = {}
        # task_signature -> [experience_id, ...] 索引
        self._sig_index: dict[str, list[str]] = defaultdict(list)

    def store(self, record: ExperienceRecord) -> ExperienceRecord:
        """存储一条经验，已存在则覆盖。"""
        with self._lock:
            self._store[record.experience_id] = record
            sig_list = self._sig_index[record.task_signature]
            if record.experience_id not in sig_list:
                sig_list.append(record.experience_id)
        return record

    def get(self, experience_id: str) -> ExperienceRecord | None:
        """按 ID 查询经验。"""
        with self._lock:
            return self._store.get(experience_id)

    def retrieve_similar(self, task_signature: str, top_k: int = 3) -> list[ExperienceRecord]:
        """按 task_signature 精确检索相似经验，按 created_at 倒序。"""
        with self._lock:
            ids = list(self._sig_index.get(task_signature, []))
            records = [self._store[eid] for eid in ids if eid in self._store]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[:top_k]

    def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        """按 goal substring 模糊匹配经验，按 created_at 倒序。

        简单实现：用 compute_task_signature 或 goal 关键词子串匹配。
        """
        sig = compute_task_signature(goal)
        # 先尝试 signature 精确匹配
        exact = self.retrieve_similar(sig, top_k=top_k)
        if exact:
            return exact

        # 降级：goal 子串模糊匹配
        goal_lower = goal.lower()
        with self._lock:
            candidates = list(self._store.values())
        matched = [
            r for r in candidates if goal_lower in r.goal.lower() or r.goal.lower() in goal_lower
        ]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        return matched[:top_k]

    def list_all(self) -> list[ExperienceRecord]:
        """列出所有经验，按 created_at 倒序。"""
        with self._lock:
            records = list(self._store.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def delete(self, experience_id: str) -> bool:
        """删除一条经验，返回是否删除成功。"""
        with self._lock:
            record = self._store.pop(experience_id, None)
            if record is None:
                return False
            sig_list = self._sig_index.get(record.task_signature, [])
            try:
                sig_list.remove(experience_id)
            except ValueError:
                pass
            return True


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_store: ExperienceStore | None = None
_store_lock = threading.Lock()


def get_experience_store() -> ExperienceStore:
    """获取全局 ExperienceStore 单例。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ExperienceStore()
    return _store


def reset_experience_store_for_tests() -> None:
    """重置全局单例（仅测试使用）。"""
    global _store
    with _store_lock:
        _store = None


# ---------------------------------------------------------------------------
# 顶层便捷函数
# ---------------------------------------------------------------------------


def store_experience(record: ExperienceRecord) -> ExperienceRecord:
    """存储经验到全局 store。"""
    return get_experience_store().store(record)


def retrieve_similar_experiences(task_signature: str, top_k: int = 3) -> list[ExperienceRecord]:
    """从全局 store 检索相似经验。"""
    return get_experience_store().retrieve_similar(task_signature, top_k=top_k)


def new_experience_id() -> str:
    """生成新的 UUID 作为经验 ID。"""
    return str(uuid.uuid4())


def compute_task_signature(goal: str) -> str:
    """计算任务签名：goal 的 SHA1 前 16 字符，lowercase。"""
    return hashlib.sha1(goal.strip().encode("utf-8")).hexdigest()[:16]


def build_experience_record(
    *,
    tenant_id: str,
    goal: str,
    plan: AgentPlan,
    tool_calls: list[dict[str, Any]] | None = None,
    outcome: str = "success",
    lessons: str = "",
    metadata: dict[str, Any] | None = None,
) -> ExperienceRecord:
    """便捷工厂：创建 ExperienceRecord（自动生成 ID、签名、时间戳）。"""
    return ExperienceRecord(
        experience_id=new_experience_id(),
        tenant_id=tenant_id,
        task_signature=compute_task_signature(goal),
        goal=goal,
        plan=plan,
        tool_calls=tool_calls or [],
        outcome=outcome,
        lessons=lessons,
        created_at=time.time(),
        metadata=metadata or {},
    )
