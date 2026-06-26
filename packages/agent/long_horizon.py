"""packages/agent/long_horizon.py — Phase R R2+ 长程任务持久化补全。

任务可跨天/跨 session 运行；随时挂起，随时续跑；管理员可见全貌。
checkpoint 频率：每完成一层 → auto-checkpoint。

R2+ 新增：
- LongRunTaskStore 抽象基类
- InMemoryLongRunTaskStore（原实现重命名，方法改 async）
- PostgresLongRunTaskStore（真实 SQL 执行）
- RedisLongRunCache（Redis 进度缓存 + fallback）
- get_long_run_store() 自动选 backend
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import AgentPlan

logger = logging.getLogger("ai_platform.agent.long_horizon")

__all__ = [
    "StepState",
    "Checkpoint",
    "LongRunTask",
    "LongRunTaskStore",
    "InMemoryLongRunTaskStore",
    "PostgresLongRunTaskStore",
    "RedisLongRunCache",
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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StepState:
        return cls(
            step_id=d["step_id"],
            status=d.get("status", "pending"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            sub_session_id=d.get("sub_session_id"),
            tool_calls_summary=d.get("tool_calls_summary") or [],
            error=d.get("error"),
        )


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Checkpoint:
        return cls(
            checkpoint_id=d["checkpoint_id"],
            task_id=d["task_id"],
            step_states=[StepState.from_dict(s) for s in d.get("step_states", [])],
            layer_index=d.get("layer_index", 0),
            created_at=d.get("created_at", 0.0),
        )


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
# Abstract base store
# ---------------------------------------------------------------------------


class LongRunTaskStore:
    """长程任务存储抽象基类。"""

    async def create(
        self,
        plan: AgentPlan,
        tenant_id: str,
        session_id: str = "",
    ) -> LongRunTask:
        raise NotImplementedError

    async def get(self, task_id: str) -> LongRunTask | None:
        raise NotImplementedError

    async def list_by_tenant(self, tenant_id: str) -> list[LongRunTask]:
        raise NotImplementedError

    async def update_status(self, task_id: str, status: str) -> bool:
        raise NotImplementedError

    async def update_step_states(self, task_id: str, step_states: list[StepState]) -> bool:
        raise NotImplementedError

    async def add_checkpoint(self, task_id: str, checkpoint: Checkpoint) -> bool:
        raise NotImplementedError

    async def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        raise NotImplementedError

    async def set_final_result(self, task_id: str, result: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def cancel(self, task_id: str) -> bool:
        raise NotImplementedError

    async def delete(self, task_id: str) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryLongRunTaskStore
# ---------------------------------------------------------------------------


class InMemoryLongRunTaskStore(LongRunTaskStore):
    """线程安全的内存长程任务存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, LongRunTask] = {}
        # tenant_id -> [task_id, ...] 索引
        self._tenant_index: dict[str, list[str]] = defaultdict(list)
        # task_id -> [Checkpoint, ...]
        self._checkpoints: dict[str, list[Checkpoint]] = defaultdict(list)

    async def create(
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
            self._tasks[task_id] = task
            self._tenant_index[tenant_id].append(task_id)
        return task

    async def get(self, task_id: str) -> LongRunTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    async def list_by_tenant(self, tenant_id: str) -> list[LongRunTask]:
        with self._lock:
            ids = list(self._tenant_index.get(tenant_id, []))
            return [self._tasks[tid] for tid in ids if tid in self._tasks]

    async def update_status(self, task_id: str, status: str) -> bool:
        if status not in VALID_TASK_STATUSES:
            return False
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = status
            task.updated_at = time.time()
            return True

    async def update_step_states(self, task_id: str, step_states: list[StepState]) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.step_states = list(step_states)
            task.updated_at = time.time()
            return True

    async def add_checkpoint(self, task_id: str, checkpoint: Checkpoint) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            self._checkpoints[task_id].append(checkpoint)
            task.checkpoints = list(self._checkpoints[task_id])
            task.updated_at = time.time()
            return True

    async def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        with self._lock:
            cps = self._checkpoints.get(task_id, [])
            if not cps:
                return None
            return cps[-1]

    async def set_final_result(self, task_id: str, result: dict[str, Any]) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.final_result = result
            task.updated_at = time.time()
            return True

    async def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in {"completed", "cancelled"}:
                return False
            task.status = "cancelled"
            task.updated_at = time.time()
            return True

    async def delete(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return False
            ids = self._tenant_index.get(task.tenant_id, [])
            try:
                ids.remove(task_id)
            except ValueError:
                pass
            self._checkpoints.pop(task_id, None)
            return True


# ---------------------------------------------------------------------------
# PostgresLongRunTaskStore
# ---------------------------------------------------------------------------


class PostgresLongRunTaskStore(LongRunTaskStore):
    """Postgres 持久化长程任务存储。

    使用 psycopg (v3) + dict_row，schema 自动创建。
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS long_run_tasks (
        task_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        plan_json JSONB NOT NULL,
        step_states_json JSONB NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        final_result_json JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_long_run_tenant ON long_run_tasks(tenant_id);

    CREATE TABLE IF NOT EXISTS long_run_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step_states_json JSONB NOT NULL,
        layer_index INTEGER NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (task_id) REFERENCES long_run_tasks(task_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON long_run_checkpoints(task_id, created_at DESC);
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._conn = None
        self._lock = threading.RLock()
        self._connect()
        self._ensure_schema()

    def _connect(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        self._conn = psycopg.connect(self._database_url, row_factory=dict_row)
        self._conn.autocommit = True

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(self._SCHEMA_SQL)

    def _row_to_task(self, row: dict[str, Any]) -> LongRunTask:
        plan_data = row["plan_json"]
        if isinstance(plan_data, str):
            plan_data = json.loads(plan_data)
        plan = AgentPlan.model_validate(plan_data)

        step_states_data = row["step_states_json"]
        if isinstance(step_states_data, str):
            step_states_data = json.loads(step_states_data)
        step_states = [StepState.from_dict(s) for s in step_states_data]

        final_result = row.get("final_result_json")
        if isinstance(final_result, str):
            final_result = json.loads(final_result) if final_result else None

        return LongRunTask(
            task_id=row["task_id"],
            tenant_id=row["tenant_id"],
            session_id=row.get("session_id", ""),
            plan=plan,
            step_states=step_states,
            status=row["status"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            checkpoints=[],  # loaded separately on demand
            final_result=final_result,
        )

    def _row_to_checkpoint(self, row: dict[str, Any]) -> Checkpoint:
        step_states_data = row["step_states_json"]
        if isinstance(step_states_data, str):
            step_states_data = json.loads(step_states_data)
        step_states = [StepState.from_dict(s) for s in step_states_data]
        return Checkpoint(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            step_states=step_states,
            layer_index=int(row["layer_index"]),
            created_at=float(row["created_at"]),
        )

    async def create(
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
        plan_json = json.dumps(plan.model_dump())
        step_states_json = json.dumps([s.to_dict() for s in step_states])
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO long_run_tasks
                        (task_id, tenant_id, session_id, plan_json, step_states_json,
                         status, created_at, updated_at, final_result_json)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, NULL)
                    """,
                    (
                        task_id,
                        tenant_id,
                        session_id,
                        plan_json,
                        step_states_json,
                        "pending",
                        now,
                        now,
                    ),
                )
        return task

    async def get(self, task_id: str) -> LongRunTask | None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM long_run_tasks WHERE task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_task(row)

    async def list_by_tenant(self, tenant_id: str) -> list[LongRunTask]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM long_run_tasks WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                )
                rows = cur.fetchall()
                return [self._row_to_task(r) for r in rows]

    async def update_status(self, task_id: str, status: str) -> bool:
        if status not in VALID_TASK_STATUSES:
            return False
        now = time.time()
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE long_run_tasks SET status = %s, updated_at = %s WHERE task_id = %s",
                    (status, now, task_id),
                )
                return cur.rowcount > 0

    async def update_step_states(self, task_id: str, step_states: list[StepState]) -> bool:
        now = time.time()
        step_states_json = json.dumps([s.to_dict() for s in step_states])
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE long_run_tasks SET step_states_json = %s::jsonb, updated_at = %s WHERE task_id = %s",
                    (step_states_json, now, task_id),
                )
                return cur.rowcount > 0

    async def add_checkpoint(self, task_id: str, checkpoint: Checkpoint) -> bool:
        step_states_json = json.dumps([s.to_dict() for s in checkpoint.step_states])
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO long_run_checkpoints
                        (checkpoint_id, task_id, step_states_json, layer_index, created_at)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        checkpoint.checkpoint_id,
                        task_id,
                        step_states_json,
                        checkpoint.layer_index,
                        checkpoint.created_at,
                    ),
                )
                return cur.rowcount > 0

    async def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM long_run_checkpoints
                    WHERE task_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_checkpoint(row)

    async def set_final_result(self, task_id: str, result: dict[str, Any]) -> bool:
        now = time.time()
        result_json = json.dumps(result)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE long_run_tasks SET final_result_json = %s::jsonb, updated_at = %s WHERE task_id = %s",
                    (result_json, now, task_id),
                )
                return cur.rowcount > 0

    async def cancel(self, task_id: str) -> bool:
        now = time.time()
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE long_run_tasks
                    SET status = 'cancelled', updated_at = %s
                    WHERE task_id = %s AND status NOT IN ('completed', 'cancelled')
                    """,
                    (now, task_id),
                )
                return cur.rowcount > 0

    async def delete(self, task_id: str) -> bool:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM long_run_tasks WHERE task_id = %s",
                    (task_id,),
                )
                return cur.rowcount > 0


# ---------------------------------------------------------------------------
# RedisLongRunCache
# ---------------------------------------------------------------------------

_REDIS_KEY_PREFIX = "ai_platform:long_run:"
_REDIS_TTL = 3600  # 1 hour


class RedisLongRunCache:
    """Redis 进度缓存，miss 时回源 store。

    缓存的 hash 结构：
      ai_platform:long_run:{task_id}  ->  { status, step_states_json, updated_at }
    """

    def __init__(self, redis_client: Any, fallback_store: LongRunTaskStore) -> None:
        self._redis = redis_client
        self._store = fallback_store

    def _key(self, task_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{task_id}"

    def _invalidate(self, task_id: str) -> None:
        try:
            self._redis.delete(self._key(task_id))
        except Exception as exc:
            logger.warning("redis invalidate failed for %s: %s", task_id, exc)

    def _fill_cache(self, task: LongRunTask) -> None:
        try:
            key = self._key(task.task_id)
            self._redis.hset(
                key,
                mapping={
                    "status": task.status,
                    "step_states_json": json.dumps([s.to_dict() for s in task.step_states]),
                    "updated_at": str(task.updated_at),
                },
            )
            self._redis.expire(key, _REDIS_TTL)
        except Exception as exc:
            logger.warning("redis fill_cache failed for %s: %s", task.task_id, exc)

    async def create(
        self,
        plan: AgentPlan,
        tenant_id: str,
        session_id: str = "",
    ) -> LongRunTask:
        task = await self._store.create(plan, tenant_id, session_id)
        self._fill_cache(task)
        return task

    async def get(self, task_id: str) -> LongRunTask | None:
        # 1. Try Redis cache
        try:
            key = self._key(task_id)
            cached = self._redis.hgetall(key)
            if cached:
                # Cache hit — get full task from store, overlay with cached status/step_states
                task = await self._store.get(task_id)
                if task is not None:
                    task.status = cached.get("status", task.status)
                    step_states_json = cached.get("step_states_json")
                    if step_states_json:
                        try:
                            steps_data = json.loads(step_states_json)
                            task.step_states = [StepState.from_dict(s) for s in steps_data]
                        except Exception:
                            pass
                    return task
        except Exception as exc:
            logger.warning("redis get failed for %s, falling back: %s", task_id, exc)

        # 2. Cache miss — query fallback store
        task = await self._store.get(task_id)
        if task is not None:
            # 3. Backfill cache
            self._fill_cache(task)
        return task

    async def list_by_tenant(self, tenant_id: str) -> list[LongRunTask]:
        return await self._store.list_by_tenant(tenant_id)

    async def update_status(self, task_id: str, status: str) -> bool:
        # 1. Write to fallback store
        ok = await self._store.update_status(task_id, status)
        # 2. Invalidate Redis cache
        self._invalidate(task_id)
        return ok

    async def update_step_states(self, task_id: str, step_states: list[StepState]) -> bool:
        ok = await self._store.update_step_states(task_id, step_states)
        self._invalidate(task_id)
        return ok

    async def add_checkpoint(self, task_id: str, checkpoint: Checkpoint) -> bool:
        ok = await self._store.add_checkpoint(task_id, checkpoint)
        self._invalidate(task_id)
        return ok

    async def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        return await self._store.get_latest_checkpoint(task_id)

    async def set_final_result(self, task_id: str, result: dict[str, Any]) -> bool:
        ok = await self._store.set_final_result(task_id, result)
        self._invalidate(task_id)
        return ok

    async def cancel(self, task_id: str) -> bool:
        ok = await self._store.cancel(task_id)
        self._invalidate(task_id)
        return ok

    async def delete(self, task_id: str) -> bool:
        ok = await self._store.delete(task_id)
        self._invalidate(task_id)
        return ok


# ---------------------------------------------------------------------------
# Global singleton — auto backend selection
# ---------------------------------------------------------------------------

_store: LongRunTaskStore | None = None
_store_lock = threading.Lock()


def get_long_run_store() -> LongRunTaskStore:
    """自动选 backend：Postgres → 内存兜底，Redis 装饰缓存。"""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        database_url = os.environ.get("DATABASE_URL", "")
        base: LongRunTaskStore
        if database_url:
            try:
                base = PostgresLongRunTaskStore(database_url)
                logger.info("long_run store backend=postgres")
            except Exception as e:
                logger.warning("postgres 不可达，回退内存: %s", e)
                base = InMemoryLongRunTaskStore()
        else:
            base = InMemoryLongRunTaskStore()
            logger.info("long_run store backend=memory")

        # 尝试装饰 Redis 缓存
        redis_url = os.environ.get("REDIS_URL", "")
        if redis_url:
            try:
                import redis

                client = redis.from_url(redis_url)
                client.ping()
                _store = RedisLongRunCache(client, base)
                logger.info("long_run cache=redis")
            except Exception as e:
                logger.warning("redis 不可达，跳过缓存: %s", e)
                _store = base
        else:
            _store = base

        return _store


def reset_long_run_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def new_task_id() -> str:
    return str(uuid.uuid4())


def new_checkpoint_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Top-level async convenience functions
# ---------------------------------------------------------------------------


async def create_long_run(
    plan: AgentPlan,
    tenant_id: str,
    session_id: str = "",
) -> LongRunTask:
    return await get_long_run_store().create(plan, tenant_id, session_id)


async def get_long_run(task_id: str) -> LongRunTask | None:
    return await get_long_run_store().get(task_id)


async def checkpoint_task(task_id: str) -> Checkpoint | None:
    """为当前任务创建 checkpoint，记录所有 step_states 和 layer_index。"""
    store = get_long_run_store()
    task = await store.get(task_id)
    if task is None:
        return None

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
    await store.add_checkpoint(task_id, checkpoint)
    return checkpoint


async def resume_task(task_id: str) -> LongRunTask | None:
    """从最新 checkpoint 恢复：加载 step_states，status → running。"""
    store = get_long_run_store()
    task = await store.get(task_id)
    if task is None:
        return None

    latest_cp = await store.get_latest_checkpoint(task_id)
    if latest_cp is not None:
        await store.update_step_states(task_id, list(latest_cp.step_states))

    await store.update_status(task_id, "running")
    return await store.get(task_id)


async def cancel_task(task_id: str) -> bool:
    return await get_long_run_store().cancel(task_id)


async def get_task_status(task_id: str) -> dict[str, Any] | None:
    """返回 task.to_dict() + progress() 合并。"""
    task = await get_long_run(task_id)
    if task is None:
        return None
    result = task.to_dict()
    result["progress"] = task.progress()
    return result
