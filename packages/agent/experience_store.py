"""packages/agent/experience_store.py — Phase R R1 Agent 经验库。

经验库 — 三层 backend：
1. InMemoryExperienceStore（默认，无依赖）
2. PostgresExperienceStore（DATABASE_URL 可达时）
3. embedding 语义检索（EmbeddingService 可用时）

降级链：
- embedding 服务不可用 → hash 精确匹配
- Postgres 不可达 → 内存 store
- 任何步骤失败不阻塞主流程
"""
from __future__ import annotations

import hashlib
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

logger = logging.getLogger("ai_platform.agent.experience_store")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


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
    embedding: list[float] | None = None  # Phase R R1+: goal 的 embedding
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "tenant_id": self.tenant_id,
            "task_signature": self.task_signature,
            "goal": self.goal,
            "plan": self.plan.model_dump() if hasattr(self.plan, "model_dump") else self.plan,
            "tool_calls": self.tool_calls,
            "outcome": self.outcome,
            "lessons": self.lessons,
            "created_at": self.created_at,
            "embedding": self.embedding,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ExperienceRecord:
        """从 Postgres 行 dict 构造（plan_json / tool_calls_json 反序列化）。"""
        plan_data = row.get("plan_json")
        if isinstance(plan_data, str):
            plan_data = json.loads(plan_data)
        tool_calls_data = row.get("tool_calls_json")
        if isinstance(tool_calls_data, str):
            tool_calls_data = json.loads(tool_calls_data)
        embedding_data = row.get("embedding")
        if isinstance(embedding_data, str):
            embedding_data = json.loads(embedding_data)
        return cls(
            experience_id=row["experience_id"],
            tenant_id=row["tenant_id"],
            task_signature=row["task_signature"],
            goal=row["goal"],
            plan=AgentPlan.model_validate(plan_data) if not isinstance(plan_data, AgentPlan) else plan_data,
            tool_calls=tool_calls_data or [],
            outcome=row["outcome"],
            lessons=row["lessons"],
            created_at=float(row["created_at"]),
            embedding=embedding_data,
            metadata={},
        )


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class ExperienceStore:
    """经验库抽象基类。"""

    async def store(self, record: ExperienceRecord) -> ExperienceRecord:
        raise NotImplementedError

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        raise NotImplementedError

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def list_all(self) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def delete(self, experience_id: str) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------


class InMemoryExperienceStore(ExperienceStore):
    """线程安全的内存经验库。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, ExperienceRecord] = {}
        # task_signature -> [experience_id, ...] 索引
        self._sig_index: dict[str, list[str]] = defaultdict(list)

    async def store(self, record: ExperienceRecord) -> ExperienceRecord:
        """存储一条经验，已存在则覆盖。"""
        with self._lock:
            self._store[record.experience_id] = record
            sig_list = self._sig_index[record.task_signature]
            if record.experience_id not in sig_list:
                sig_list.append(record.experience_id)
        return record

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        """按 ID 查询经验。"""
        with self._lock:
            return self._store.get(experience_id)

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        """检索相似经验。

        - 若 task_embedding 提供 → 用 cosine similarity 排序
        - 否则 → 用 task_signature 精确匹配
        """
        with self._lock:
            if task_embedding is not None:
                # embedding 语义检索：扫所有 records，算 cosine
                candidates = list(self._store.values())
            else:
                # 降级：task_signature 精确匹配
                ids = list(self._sig_index.get(task_signature, []))
                candidates = [self._store[eid] for eid in ids if eid in self._store]

        if task_embedding is not None and candidates:
            # cosine similarity 排序
            scored = []
            for r in candidates:
                if r.embedding is None:
                    continue
                score = _cosine_similarity(task_embedding, r.embedding)
                scored.append((r, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [r for r, _ in scored[:top_k]]

        # 无 embedding 或降级路径：按 created_at 倒序
        candidates.sort(key=lambda r: r.created_at, reverse=True)
        return candidates[:top_k]

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        """按 goal substring 模糊匹配。"""
        sig = compute_task_signature(goal)
        exact = await self.retrieve_similar(sig, top_k=top_k)
        if exact:
            return exact

        goal_lower = goal.lower()
        with self._lock:
            candidates = list(self._store.values())
        matched = [r for r in candidates if goal_lower in r.goal.lower() or r.goal.lower() in goal_lower]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        return matched[:top_k]

    async def list_all(self) -> list[ExperienceRecord]:
        with self._lock:
            return list(self._store.values())

    async def delete(self, experience_id: str) -> bool:
        with self._lock:
            record = self._store.pop(experience_id, None)
            if record is None:
                return False
            sig_list = self._sig_index.get(record.task_signature, [])
            if experience_id in sig_list:
                sig_list.remove(experience_id)
            return True


# ---------------------------------------------------------------------------
# Postgres 实现
# ---------------------------------------------------------------------------


class PostgresExperienceStore(ExperienceStore):
    """Postgres 持久化经验库。

    Schema:
        experiences(experience_id, tenant_id, task_signature, goal,
                    plan_json, tool_calls_json, outcome, lessons,
                    embedding, created_at)
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._conn = self._connect()
        self._ensure_schema()

    def _connect(self) -> Any:
        import psycopg  # type: ignore[import-untyped]
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        """创建表和索引（IF NOT EXISTS）。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    task_signature TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    plan_json JSONB NOT NULL,
                    tool_calls_json JSONB NOT NULL,
                    outcome TEXT NOT NULL,
                    lessons TEXT NOT NULL,
                    embedding JSONB,
                    created_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiences_tenant ON experiences(tenant_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiences_signature ON experiences(task_signature)"
            )
        self._conn.commit()

    async def store(self, record: ExperienceRecord) -> ExperienceRecord:
        plan_json = record.plan.model_dump() if hasattr(record.plan, "model_dump") else record.plan
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiences
                    (experience_id, tenant_id, task_signature, goal,
                     plan_json, tool_calls_json, outcome, lessons, embedding, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (experience_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    task_signature = EXCLUDED.task_signature,
                    goal = EXCLUDED.goal,
                    plan_json = EXCLUDED.plan_json,
                    tool_calls_json = EXCLUDED.tool_calls_json,
                    outcome = EXCLUDED.outcome,
                    lessons = EXCLUDED.lessons,
                    embedding = EXCLUDED.embedding,
                    created_at = EXCLUDED.created_at
                """,
                (
                    record.experience_id,
                    record.tenant_id,
                    record.task_signature,
                    record.goal,
                    json.dumps(plan_json),
                    json.dumps(record.tool_calls),
                    record.outcome,
                    record.lessons,
                    json.dumps(record.embedding) if record.embedding else None,
                    record.created_at,
                ),
            )
        self._conn.commit()
        return record

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE experience_id = %s",
                (experience_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return ExperienceRecord.from_row(row)

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        if task_embedding is not None:
            # embedding 检索：取所有同 signature 的，Python 算 cosine
            # 优化：先按 signature 过滤减少数据量，再算 cosine
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM experiences WHERE task_signature = %s ORDER BY created_at DESC",
                    (task_signature,),
                )
                rows = cur.fetchall()
            if not rows:
                # 降级到全表扫
                with self._conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM experiences ORDER BY created_at DESC LIMIT 100"
                    )
                    rows = cur.fetchall()
            records = [ExperienceRecord.from_row(r) for r in rows]
            scored = []
            for r in records:
                if r.embedding is None:
                    continue
                score = _cosine_similarity(task_embedding, r.embedding)
                scored.append((r, score))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [r for r, _ in scored[:top_k]]

        # 无 embedding：signature 精确匹配
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE task_signature = %s ORDER BY created_at DESC LIMIT %s",
                (task_signature, top_k),
            )
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        sig = compute_task_signature(goal)
        exact = await self.retrieve_similar(sig, top_k=top_k)
        if exact:
            return exact
        # 降级：ILIKE 模糊匹配
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE goal ILIKE %s ORDER BY created_at DESC LIMIT %s",
                (f"%{goal}%", top_k),
            )
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def list_all(self) -> list[ExperienceRecord]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM experiences ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def delete(self, experience_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM experiences WHERE experience_id = %s",
                (experience_id,),
            )
            deleted = cur.rowcount
        self._conn.commit()
        return deleted > 0


# ---------------------------------------------------------------------------
# cosine similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的 cosine similarity。维度不匹配返回 0。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# 全局单例 + backend 自动选择
# ---------------------------------------------------------------------------


_store: ExperienceStore | None = None
_store_lock = threading.Lock()


def get_experience_store() -> ExperienceStore:
    """获取全局 ExperienceStore 单例。

    Backend 选择：
    1. DATABASE_URL 可达 → PostgresExperienceStore
    2. 否则 → InMemoryExperienceStore
    """
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            database_url = os.environ.get("DATABASE_URL", "")
            if database_url:
                try:
                    _store = PostgresExperienceStore(database_url)
                    logger.info("experience store backend=postgres")
                except Exception as e:
                    logger.warning("postgres 不可达，回退内存 experience store: %s", e)
                    _store = InMemoryExperienceStore()
                    logger.info("experience store backend=memory")
            else:
                _store = InMemoryExperienceStore()
                logger.info("experience store backend=memory")
    return _store


def reset_experience_store_for_tests() -> None:
    """重置全局单例（仅测试使用）。"""
    global _store
    with _store_lock:
        _store = None


# ---------------------------------------------------------------------------
# 顶层便捷函数（async）
# ---------------------------------------------------------------------------


async def store_experience(record: ExperienceRecord) -> ExperienceRecord:
    """存储经验到全局 store。"""
    return await get_experience_store().store(record)


async def retrieve_similar_experiences(
    task_signature: str,
    task_embedding: list[float] | None = None,
    top_k: int = 3,
) -> list[ExperienceRecord]:
    """从全局 store 检索相似经验。"""
    return await get_experience_store().retrieve_similar(
        task_signature, task_embedding=task_embedding, top_k=top_k
    )


async def compute_task_embedding(goal: str) -> list[float] | None:
    """调 EmbeddingService 计算 goal 的 embedding。失败返回 None。"""
    try:
        from packages.embedding.service import get_embedding_service

        service = get_embedding_service()
        if service is None:
            return None
        from apps.gateway.settings import get_settings

        settings = get_settings()
        model_id = "text-embedding-3-small"
        # 尝试从 settings 读 embedding_model
        emb_model = getattr(settings, "embedding_model", None)
        if emb_model:
            model_id = emb_model
        return await service.embed_one(model_id, goal, tenant_id="system")
    except Exception as exc:
        logger.warning("compute_task_embedding failed: %s", exc)
        return None


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
    embedding: list[float] | None = None,
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
        embedding=embedding,
        metadata=metadata or {},
    )
