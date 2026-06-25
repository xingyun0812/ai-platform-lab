"""packages/agent/long_horizon.py — Phase R R2 跨 session 长程任务。

任务可跨天/跨 session 运行；随时挂起，随时续跑；管理员可见全貌。
checkpoint 频率：每完成一层 → auto-checkpoint。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import AgentPlan

__all__ = [
    "StepState",
    "Checkpoint",
    "LongRunTask",
    "LongRunTaskStore",
    "get_long_run_store",
    "reset_long_run_store_for_tests",
    "create_long_run",
    "get_long_run",
    "checkpoint_task",
    "resume_task",
    "cancel_task",
    "get_task_status",
    "new_task_id",
    "new_checkpoint_id",
]

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

VALID_STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped"}
VALID_TASK_STATUSES = {"pending", "running", "paused", "completed", "failed", "cancelled"}


@dataclass
class StepState:
    """单步执行状态快照。"""

    step_id: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    started_at: float | None = None
    completed_at: float | None = None
    sub_session_id: str | None = None
    tool_calls_summary: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "sub_session_id": self.sub_session_id,
            "tool_calls_summary": list(self.tool_calls_summary),
            "error": self.error,
        }


@dataclass
class Checkpoint:
    """层级 checkpoint 快照。"""

    checkpoint_id: str  # UUID
    task_id: str
    step_states: list[StepState]
    layer_index: int  # 已完成的 layer 数
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "step_states": [s.to_dict() for s in self.step_states],
            "layer_index": self.layer_index,
            "created_at": self.created_at,
        }


@dataclass
class LongRunTask:
    """跨 session 长程任务。"""

    task_id: str  # UUID
    tenant_id: str
    session_id: str
    plan: AgentPlan
    step_states: list[StepState]
    status: str = "pending"  # pending | running | paused | completed | failed | cancelled
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "plan": self.plan.model_dump(),
            "step_states": [s.to_dict() for s in self.step_states],
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "checkpoints": [c.to_dict() for c in self.checkpoints],
            "final_result": self.final_result,
            "metadata": dict(self.metadata),
        }

    def progress(self) -> dict[str, Any]:
        """返回 {total, completed, failed, pending, percent}。"""
        total = len(self.step_states)
        completed = sum(1 for s in self.step_states if s.status == "completed")
        failed = sum(1 for s in self.step_states if s.status == "failed")
        pending = sum(1 for s in self.step_states if s.status == "pending")
        percent = round(completed / total * 100, 1) if total > 0 else 0.0
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "percent": percent,
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class LongRunTaskStore:
    """线程安全的内存长程任务存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, LongRunTask] = {}
        # tenant_id -> [task_id, ...] 索引
        self._tenant_index: dict[str, list[str]] = defaultdict(list)

    def create(
        self,
        plan: AgentPlan,
        tenant_id: str,
        session_id: str = "",
    ) -> LongRunTask:
        task_id = new_task_id()
        step_states = [StepState(step_id=s.id) for s in plan.steps]
        now = time.time()
        task = LongRunTask(
            task_id=task_id,
            tenant_id=tenant_id,
            session_id=session_id,
            plan=plan,
            step_states=step_states,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._store[task_id] = task
            self._tenant_index[tenant_id].append(task_id)
        return task

    def get(self, task_id: str) -> LongRunTask | None:
        with self._lock:
            return self._store.get(task_id)

    def list_by_tenant(self, tenant_id: str) -> list[LongRunTask]:
        with self._lock:
            ids = list(self._tenant_index.get(tenant_id, []))
            return [self._store[tid] for tid in ids if tid in self._store]

    def update_status(self, task_id: str, status: str) -> bool:
        if status not in VALID_TASK_STATUSES:
            return False
        with self._lock:
            task = self._store.get(task_id)
            if task is None:
                return False
            task.status = status
            task.updated_at = time.time()
            return True

    def update_step_states(self, task_id: str, step_states: list[StepState]) -> bool:
        with self._lock:
            task = self._store.get(task_id)
            if task is None:
                return False
            task.step_states = list(step_states)
            task.updated_at = time.time()
            return True

    def add_checkpoint(self, task_id: str, checkpoint: Checkpoint) -> bool:
        with self._lock:
            task = self._store.get(task_id)
            if task is None:
                return False
            task.checkpoints.append(checkpoint)
            task.updated_at = time.time()
            return True

    def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        with self._lock:
            task = self._store.get(task_id)
            if task is None or not task.checkpoints:
                return None
            return task.checkpoints[-1]

    def set_final_result(self, task_id: str, result: dict[str, Any]) -> bool:
        with self._lock:
            task = self._store.get(task_id)
            if task is None:
                return False
            task.final_result = result
            task.updated_at = time.time()
            return True

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._store.get(task_id)
            if task is None:
                return False
            if task.status in {"completed", "cancelled"}:
                return False
            task.status = "cancelled"
            task.updated_at = time.time()
            return True

    def delete(self, task_id: str) -> bool:
        with self._lock:
            task = self._store.pop(task_id, None)
            if task is None:
                return False
            ids = self._tenant_index.get(task.tenant_id, [])
            try:
                ids.remove(task_id)
            except ValueError:
                pass
            return True


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_store: LongRunTaskStore | None = None
_store_lock = threading.Lock()


def get_long_run_store() -> LongRunTaskStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LongRunTaskStore()
        return _store


def reset_long_run_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = LongRunTaskStore()


# ---------------------------------------------------------------------------
# Top-level convenience functions
# ---------------------------------------------------------------------------


def new_task_id() -> str:
    return str(uuid.uuid4())


def new_checkpoint_id() -> str:
    return str(uuid.uuid4())


def create_long_run(
    plan: AgentPlan,
    tenant_id: str,
    session_id: str = "",
) -> LongRunTask:
    return get_long_run_store().create(plan, tenant_id, session_id)


def get_long_run(task_id: str) -> LongRunTask | None:
    return get_long_run_store().get(task_id)


def checkpoint_task(task_id: str) -> Checkpoint | None:
    """为当前任务创建 checkpoint，记录所有 step_states 和 layer_index。"""
    store = get_long_run_store()
    task = store.get(task_id)
    if task is None:
        return None

    # 计算已完成的 layer 数（依据 step_states 中 completed step 数量推算）
    completed_count = sum(1 for s in task.step_states if s.status == "completed")

    checkpoint = Checkpoint(
        checkpoint_id=new_checkpoint_id(),
        task_id=task_id,
        step_states=[
            StepState(
                step_id=s.step_id,
                status=s.status,
                started_at=s.started_at,
                completed_at=s.completed_at,
                sub_session_id=s.sub_session_id,
                tool_calls_summary=list(s.tool_calls_summary),
                error=s.error,
            )
            for s in task.step_states
        ],
        layer_index=completed_count,
        created_at=time.time(),
    )
    store.add_checkpoint(task_id, checkpoint)
    return checkpoint


def resume_task(task_id: str) -> LongRunTask | None:
    """从最新 checkpoint 恢复：加载 step_states，status → running。"""
    store = get_long_run_store()
    task = store.get(task_id)
    if task is None:
        return None

    latest_cp = store.get_latest_checkpoint(task_id)
    if latest_cp is not None:
        # Restore step_states from checkpoint
        store.update_step_states(task_id, list(latest_cp.step_states))

    store.update_status(task_id, "running")
    return store.get(task_id)


def cancel_task(task_id: str) -> bool:
    return get_long_run_store().cancel(task_id)


def get_task_status(task_id: str) -> dict[str, Any] | None:
    """返回 task.to_dict() + progress() 合并。"""
    task = get_long_run(task_id)
    if task is None:
        return None
    result = task.to_dict()
    result["progress"] = task.progress()
    return result
