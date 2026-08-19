"""记忆归档存储 — ArchiveStore for pre-purge archiving.

ArchiveStore 在 purge 删除前将记录归档到 memory_archive 表。
InMemoryArchiveStore 提供测试用内存实现。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.memory.store import MemoryRecord

logger = logging.getLogger("ai_platform.memory.archive")


@dataclass
class ArchivedRecord:
    """归档记录数据模型。"""

    archive_id: str
    memory_id: str
    tenant_id: str
    scope: str
    scope_id: str
    content: str
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    archived_at: float = 0.0
    purge_reason: str = ""
    original_weight: float = 0.0
    access_count: int = 0


def _archive_id() -> str:
    return f"arch-{uuid.uuid4().hex[:16]}"


class ArchiveStore:
    """归档存储基类 — 在 purge 删除前保存记录副本。"""

    async def archive(self, record: MemoryRecord, *, purge_reason: str) -> str:
        """归档一条记录。返回 archive_id。"""
        raise NotImplementedError

    async def list_archived(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[ArchivedRecord]:
        raise NotImplementedError

    async def count_archived(self, *, tenant_id: str) -> int:
        raise NotImplementedError

    async def get_archived(self, archive_id: str) -> ArchivedRecord | None:
        raise NotImplementedError


class InMemoryArchiveStore(ArchiveStore):
    """进程内归档存储（测试用）。"""

    def __init__(self) -> None:
        self._records: dict[str, ArchivedRecord] = {}

    async def archive(self, record: MemoryRecord, *, purge_reason: str) -> str:
        aid = _archive_id()
        archived = ArchivedRecord(
            archive_id=aid,
            memory_id=record.memory_id,
            tenant_id=record.tenant_id,
            scope=record.scope,
            scope_id=record.scope_id,
            content=record.content,
            summary=record.summary,
            metadata=record.metadata.copy() if record.metadata else {},
            created_at=record.created_at,
            archived_at=time.time(),
            purge_reason=purge_reason,
            original_weight=record.weight,
            access_count=record.access_count,
        )
        self._records[aid] = archived
        return aid

    async def list_archived(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[ArchivedRecord]:
        results = [
            r
            for r in self._records.values()
            if r.tenant_id == tenant_id and r.scope == scope and r.scope_id == scope_id
        ]
        results.sort(key=lambda r: r.archived_at, reverse=True)
        return results[:limit]

    async def count_archived(self, *, tenant_id: str) -> int:
        return sum(1 for r in self._records.values() if r.tenant_id == tenant_id)

    async def get_archived(self, archive_id: str) -> ArchivedRecord | None:
        return self._records.get(archive_id)


# ------------------------------------------------------------------------- #
# Postgres 实现
# ------------------------------------------------------------------------- #

ARCHIVE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_archive (
    archive_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    content TEXT,
    summary TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ DEFAULT NOW(),
    purge_reason TEXT NOT NULL,
    original_weight DOUBLE PRECISION,
    access_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_archive_tenant
    ON memory_archive (tenant_id, scope, scope_id);
"""


class PostgresArchiveStore(ArchiveStore):
    """Postgres 持久化归档存储。"""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._init_schema()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(ARCHIVE_SCHEMA_SQL)
                conn.commit()
            logger.info("archive store schema initialized")
        except Exception as e:
            logger.error("archive store schema init failed: %s", e)
            raise

    @staticmethod
    def _row_to_archived(row: dict[str, Any]) -> ArchivedRecord:
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if meta is None:
            meta = {}
        created_raw = row.get("created_at")
        created_at = (
            created_raw.timestamp()
            if hasattr(created_raw, "timestamp")
            else float(created_raw or 0.0)
        )
        archived_raw = row.get("archived_at")
        archived_at = (
            archived_raw.timestamp()
            if hasattr(archived_raw, "timestamp")
            else float(archived_raw or 0.0)
        )
        return ArchivedRecord(
            archive_id=str(row["archive_id"]),
            memory_id=str(row["memory_id"]),
            tenant_id=str(row["tenant_id"]),
            scope=str(row["scope"]),
            scope_id=str(row["scope_id"]),
            content=str(row.get("content", "")),
            summary=row.get("summary"),
            metadata=meta,
            created_at=created_at,
            archived_at=archived_at,
            purge_reason=str(row.get("purge_reason", "")),
            original_weight=float(row.get("original_weight", 0.0)),
            access_count=int(row.get("access_count", 0)),
        )

    async def archive(self, record: MemoryRecord, *, purge_reason: str) -> str:
        aid = _archive_id()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_archive
                        (archive_id, memory_id, tenant_id, scope, scope_id,
                         content, summary, metadata, created_at, archived_at,
                         purge_reason, original_weight, access_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(),
                            %s, %s, %s)
                    """,
                    (
                        aid,
                        record.memory_id,
                        record.tenant_id,
                        record.scope,
                        record.scope_id,
                        record.content,
                        record.summary,
                        json.dumps(record.metadata),
                        record.created_at,
                        purge_reason,
                        record.weight,
                        record.access_count,
                    ),
                )
                conn.commit()
            return aid
        except Exception as e:
            logger.error("archive failed for %s: %s", record.memory_id, e)
            raise

    async def list_archived(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[ArchivedRecord]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM memory_archive
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                    ORDER BY archived_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, scope, scope_id, limit),
                ).fetchall()
            return [self._row_to_archived(r) for r in rows]
        except Exception as e:
            logger.error("list_archived failed: %s", e)
            return []

    async def count_archived(self, *, tenant_id: str) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM memory_archive WHERE tenant_id = %s",
                    (tenant_id,),
                ).fetchone()
            return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error("count_archived failed: %s", e)
            return 0

    async def get_archived(self, archive_id: str) -> ArchivedRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM memory_archive WHERE archive_id = %s",
                    (archive_id,),
                ).fetchone()
            if row is None:
                return None
            return self._row_to_archived(row)
        except Exception as e:
            logger.error("get_archived failed: %s", e)
            return None


# ------------------------------------------------------------------------- #
# 全局单例
# ------------------------------------------------------------------------- #

_global_archive_store: ArchiveStore | None = None


def get_archive_store() -> ArchiveStore | None:
    return _global_archive_store


def init_archive_store(*, database_url: str | None = None) -> ArchiveStore:
    global _global_archive_store
    if database_url:
        try:
            _global_archive_store = PostgresArchiveStore(database_url)
            logger.info("archive store backend=postgres")
            return _global_archive_store
        except Exception as e:
            logger.warning("postgres archive 不可达，回退内存 archive store: %s", e)
    _global_archive_store = InMemoryArchiveStore()
    logger.info("archive store backend=memory")
    return _global_archive_store


def reset_archive_store_for_tests() -> None:
    global _global_archive_store
    _global_archive_store = None
