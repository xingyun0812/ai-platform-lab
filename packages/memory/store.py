"""长记忆存储 — Postgres 主存 + Redis 热缓存 + 进程内兜底。

MemoryRecord：
    memory_id, tenant_id, scope, scope_id, content, summary, embedding, metadata, created_at, expires_at

scope 三级：
    session — 单会话短期（自动 TTL）
    user    — 跨会话中期（用户级长期偏好/历史）
    tenant  — 租户级共享知识（团队级）

检索模式：
    keyword — content LIKE '%query%' 模糊匹配（默认，无依赖）
    semantic — embedding cosine similarity（需 embedding 服务）
"""

from __future__ import annotations

import dataclasses as _dc
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.memory.metrics import get_memory_metrics

logger = logging.getLogger("ai_platform.memory")


@dataclass
class MemoryRecord:
    memory_id: str
    tenant_id: str
    scope: str  # session | user | tenant
    scope_id: str
    content: str
    summary: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _dc.asdict(self)

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.time()) > self.expires_at


def _gen_id() -> str:
    return f"mem-{uuid.uuid4().hex[:16]}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


class MemoryStore:
    """长记忆存储基类。"""

    def __init__(self) -> None:
        self._metrics = get_memory_metrics()

    async def add(self, record: MemoryRecord) -> str:
        raise NotImplementedError

    async def get(self, memory_id: str) -> MemoryRecord | None:
        raise NotImplementedError

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    async def delete(self, memory_id: str) -> bool:
        raise NotImplementedError

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        raise NotImplementedError


# --------------------------------------------------------------------- #
# 进程内实现
# --------------------------------------------------------------------- #

class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()
        # _records[(tenant_id, scope, scope_id)] = list[MemoryRecord]
        self._records: dict[tuple[str, str, str], list[MemoryRecord]] = {}
        self._by_id: dict[str, MemoryRecord] = {}

    async def add(self, record: MemoryRecord) -> str:
        with self._lock:
            key = (record.tenant_id, record.scope, record.scope_id)
            self._records.setdefault(key, []).append(record)
            self._by_id[record.memory_id] = record
        self._metrics.record_add(tenant_id=record.tenant_id, scope=record.scope)
        return record.memory_id

    async def get(self, memory_id: str) -> MemoryRecord | None:
        with self._lock:
            r = self._by_id.get(memory_id)
            if r is None:
                return None
            if r.is_expired():
                return None
            return r

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        import time as _time

        start = _time.perf_counter()
        with self._lock:
            key = (tenant_id, scope, scope_id)
            records = [r for r in self._records.get(key, []) if not r.is_expired()]
        # 评分
        scored: list[tuple[float, MemoryRecord]] = []
        q_lower = query.lower()
        for r in records:
            if query_embedding is not None and r.embedding is not None:
                sim = _cosine_similarity(query_embedding, r.embedding)
                scored.append((sim, r))
            else:
                # keyword 模糊匹配：简单子串命中数
                content_lower = r.content.lower()
                if q_lower in content_lower:
                    score = 1.0
                else:
                    # 分词命中数
                    q_tokens = [t for t in q_lower.split() if t]
                    if not q_tokens:
                        score = 0.0
                    else:
                        hits = sum(1 for t in q_tokens if t in content_lower)
                        score = hits / len(q_tokens)
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _s, r in scored[:top_k] if _s > 0]
        latency_ms = (_time.perf_counter() - start) * 1000
        self._metrics.record_search(tenant_id=tenant_id, scope=scope)
        self._metrics.record_search_latency(
            tenant_id=tenant_id, scope=scope, latency_ms=latency_ms
        )
        return results

    async def delete(self, memory_id: str) -> bool:
        with self._lock:
            r = self._by_id.pop(memory_id, None)
            if r is None:
                return False
            key = (r.tenant_id, r.scope, r.scope_id)
            bucket = self._records.get(key, [])
            self._records[key] = [x for x in bucket if x.memory_id != memory_id]
            return True

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        with self._lock:
            key = (tenant_id, scope, scope_id)
            records = [r for r in self._records.get(key, []) if not r.is_expired()]
        return records[:limit]


# --------------------------------------------------------------------- #
# Postgres 实现
# --------------------------------------------------------------------- #

class PostgresMemoryStore(MemoryStore):
    """Postgres 持久化存储。

    Schema:
        CREATE TABLE agent_memories (
            memory_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            embedding JSONB,         -- [f1, f2, ...]
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ
        );
        CREATE INDEX idx_mem_scope ON agent_memories (tenant_id, scope, scope_id);
        CREATE INDEX idx_mem_expires ON agent_memories (expires_at);
    """

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS agent_memories (
        memory_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        content TEXT NOT NULL,
        summary TEXT,
        embedding JSONB,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_mem_scope
        ON agent_memories (tenant_id, scope, scope_id);
    """

    def __init__(self, database_url: str) -> None:
        super().__init__()
        self._url = database_url
        self._init_schema()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(self.SCHEMA_SQL)
                conn.commit()
            logger.info("memory store schema initialized")
        except Exception as e:
            logger.error("memory store schema init failed: %s", e)
            raise

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
        emb = row.get("embedding")
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                emb = None
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if meta is None:
            meta = {}
        created_at_raw = row.get("created_at")
        created_at = (
            created_at_raw.timestamp()
            if hasattr(created_at_raw, "timestamp")
            else float(created_at_raw or time.time())
        )
        expires_raw = row.get("expires_at")
        expires_at = None
        if expires_raw is not None:
            expires_at = (
                expires_raw.timestamp()
                if hasattr(expires_raw, "timestamp")
                else float(expires_raw)
            )
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            tenant_id=str(row["tenant_id"]),
            scope=str(row["scope"]),
            scope_id=str(row["scope_id"]),
            content=str(row["content"]),
            summary=row.get("summary"),
            embedding=emb,
            metadata=meta if isinstance(meta, dict) else {},
            created_at=created_at,
            expires_at=expires_at,
        )

    async def add(self, record: MemoryRecord) -> str:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_memories
                        (memory_id, tenant_id, scope, scope_id, content, summary,
                         embedding, metadata, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.memory_id,
                        record.tenant_id,
                        record.scope,
                        record.scope_id,
                        record.content,
                        record.summary,
                        json.dumps(record.embedding) if record.embedding else None,
                        json.dumps(record.metadata),
                        record.created_at,
                        record.expires_at,
                    ),
                )
                conn.commit()
            self._metrics.record_add(tenant_id=record.tenant_id, scope=record.scope)
            return record.memory_id
        except Exception as e:
            logger.error("memory add failed: %s", e)
            self._metrics.record_store_error(
                tenant_id=record.tenant_id, scope=record.scope
            )
            raise

    async def get(self, memory_id: str) -> MemoryRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM agent_memories WHERE memory_id = %s",
                    (memory_id,),
                ).fetchone()
            if row is None:
                return None
            r = self._row_to_record(row)
            if r.is_expired():
                return None
            return r
        except Exception as e:
            logger.error("memory get failed: %s", e)
            return None

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        import time as _time

        start = _time.perf_counter()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, scope, scope_id, max(top_k * 4, 20)),
                ).fetchall()
        except Exception as e:
            logger.error("memory search failed: %s", e)
            self._metrics.record_store_error(tenant_id=tenant_id, scope=scope)
            return []
        records = [self._row_to_record(r) for r in rows]
        # 内存中打分（与 InMemory 一致）
        scored: list[tuple[float, MemoryRecord]] = []
        q_lower = query.lower()
        for r in records:
            if query_embedding is not None and r.embedding is not None:
                sim = _cosine_similarity(query_embedding, r.embedding)
                scored.append((sim, r))
            else:
                content_lower = r.content.lower()
                if q_lower in content_lower:
                    score = 1.0
                else:
                    q_tokens = [t for t in q_lower.split() if t]
                    if not q_tokens:
                        score = 0.0
                    else:
                        hits = sum(1 for t in q_tokens if t in content_lower)
                        score = hits / len(q_tokens)
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _s, r in scored[:top_k] if _s > 0]
        latency_ms = (_time.perf_counter() - start) * 1000
        self._metrics.record_search(tenant_id=tenant_id, scope=scope)
        self._metrics.record_search_latency(
            tenant_id=tenant_id, scope=scope, latency_ms=latency_ms
        )
        return results

    async def delete(self, memory_id: str) -> bool:
        try:
            with self._connect() as conn:
                result = conn.execute(
                    "DELETE FROM agent_memories WHERE memory_id = %s",
                    (memory_id,),
                )
                conn.commit()
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error("memory delete failed: %s", e)
            return False

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, scope, scope_id, limit),
                ).fetchall()
            return [self._row_to_record(r) for r in rows]
        except Exception as e:
            logger.error("memory list failed: %s", e)
            return []


# --------------------------------------------------------------------- #
# 工厂与全局单例
# --------------------------------------------------------------------- #

_global_store: MemoryStore | None = None
_global_lock = threading.Lock()


def init_memory_store(
    *,
    database_url: str | None = None,
) -> MemoryStore:
    """初始化全局 MemoryStore。

    优先级：
    1. DATABASE_URL 可达 → PostgresMemoryStore
    2. 否则 → InMemoryMemoryStore
    """
    global _global_store
    with _global_lock:
        if database_url:
            try:
                _global_store = PostgresMemoryStore(database_url)
                logger.info("memory store backend=postgres")
                return _global_store
            except Exception as e:
                logger.warning(
                    "postgres 不可达，回退进程内 memory store: %s", e
                )
        _global_store = InMemoryMemoryStore()
        logger.info("memory store backend=memory")
        return _global_store


def get_memory_store() -> MemoryStore | None:
    return _global_store


def reset_memory_store_for_tests() -> None:
    global _global_store
    with _global_lock:
        _global_store = None
