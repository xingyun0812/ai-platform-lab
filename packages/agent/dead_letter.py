"""packages/agent/dead_letter.py — S5: Dead-letter execution record store.

Records failed tool/step executions that exceed retry limits or encounter
non-recoverable errors, for later inspection, replay, or alerting.

Follows the same ABC + InMemory + Postgres pattern as LongRunTaskStore
in long_horizon.py.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.agent.dead_letter")

__all__ = [
    "DeadLetterRecord",
    "DeadLetterStore",
    "InMemoryDeadLetterStore",
    "PostgresDeadLetterStore",
    "get_dead_letter_store",
    "reset_dead_letter_store_for_tests",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DeadLetterRecord:
    """一条死信记录，对应一次失败的工具/步骤执行。"""

    id: str
    task_id: str
    step_id: str | None = None
    tool_name: str | None = None
    error_code: str = "UNKNOWN"
    error_message: str = ""
    context_json: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tool_name": self.tool_name,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "context_json": self.context_json,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeadLetterRecord:
        return cls(
            id=d["id"],
            task_id=d["task_id"],
            step_id=d.get("step_id"),
            tool_name=d.get("tool_name"),
            error_code=d.get("error_code", "UNKNOWN"),
            error_message=d.get("error_message", ""),
            context_json=d.get("context_json"),
            created_at=d.get("created_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Abstract base store
# ---------------------------------------------------------------------------


class DeadLetterStore(ABC):
    """死信记录存储抽象基类。"""

    @abstractmethod
    def add_dead_letter(self, record: DeadLetterRecord) -> str:
        """写入一条死信记录，返回其 id。"""
        ...

    @abstractmethod
    def list_dead_letters(self, task_id: str) -> list[DeadLetterRecord]:
        """查询指定 task 的所有死信记录。"""
        ...

    @abstractmethod
    def get_dead_letter(self, id: str) -> DeadLetterRecord | None:
        """根据 id 查询单条死信记录。"""
        ...


# ---------------------------------------------------------------------------
# InMemoryDeadLetterStore
# ---------------------------------------------------------------------------


class InMemoryDeadLetterStore(DeadLetterStore):
    """线程安全的内存死信记录存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, DeadLetterRecord] = {}
        self._task_index: dict[str, list[str]] = {}  # task_id -> [id, ...]

    def add_dead_letter(self, record: DeadLetterRecord) -> str:
        with self._lock:
            self._records[record.id] = record
            self._task_index.setdefault(record.task_id, []).append(record.id)
        return record.id

    def list_dead_letters(self, task_id: str) -> list[DeadLetterRecord]:
        with self._lock:
            ids = list(self._task_index.get(task_id, []))
            return [self._records[rid] for rid in ids if rid in self._records]

    def get_dead_letter(self, id: str) -> DeadLetterRecord | None:
        with self._lock:
            return self._records.get(id)


# ---------------------------------------------------------------------------
# PostgresDeadLetterStore
# ---------------------------------------------------------------------------


class PostgresDeadLetterStore(DeadLetterStore):
    """Postgres 持久化死信记录存储。

    使用 psycopg (v3) + dict_row，schema 自动创建。
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS dead_letter_executions (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step_id TEXT,
        tool_name TEXT,
        error_code TEXT NOT NULL,
        error_message TEXT NOT NULL,
        context_json JSONB,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_dead_letter_task ON dead_letter_executions(task_id);
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

    def _row_to_record(self, row: dict[str, Any]) -> DeadLetterRecord:
        context = row.get("context_json")
        if isinstance(context, str):
            context = json.loads(context) if context else None
        return DeadLetterRecord(
            id=row["id"],
            task_id=row["task_id"],
            step_id=row.get("step_id"),
            tool_name=row.get("tool_name"),
            error_code=row.get("error_code", "UNKNOWN"),
            error_message=row.get("error_message", ""),
            context_json=context,
            created_at=float(row["created_at"]),
        )

    def add_dead_letter(self, record: DeadLetterRecord) -> str:
        context_json = json.dumps(record.context_json) if record.context_json else None
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO dead_letter_executions
                        (id, task_id, step_id, tool_name, error_code, error_message,
                         context_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        record.id,
                        record.task_id,
                        record.step_id,
                        record.tool_name,
                        record.error_code,
                        record.error_message,
                        context_json,
                        record.created_at,
                    ),
                )
        return record.id

    def list_dead_letters(self, task_id: str) -> list[DeadLetterRecord]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM dead_letter_executions WHERE task_id = %s ORDER BY created_at DESC",
                    (task_id,),
                )
                return [self._row_to_record(r) for r in cur.fetchall()]

    def get_dead_letter(self, id: str) -> DeadLetterRecord | None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM dead_letter_executions WHERE id = %s",
                    (id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)


# ---------------------------------------------------------------------------
# Global singleton — auto backend selection
# ---------------------------------------------------------------------------

_store: DeadLetterStore | None = None
_store_lock = threading.Lock()


def get_dead_letter_store() -> DeadLetterStore:
    """自动选 backend：Postgres -> 内存兜底。"""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        database_url = os.environ.get("DATABASE_URL", "")
        if database_url:
            try:
                _store = PostgresDeadLetterStore(database_url)
                logger.info("dead_letter store backend=postgres")
            except Exception as e:
                logger.warning("postgres 不可达，回退内存: %s", e)
                _store = InMemoryDeadLetterStore()
        else:
            _store = InMemoryDeadLetterStore()
            logger.info("dead_letter store backend=memory")

        return _store


def reset_dead_letter_store_for_tests() -> None:
    """测试用：重置全局 store。"""
    global _store
    with _store_lock:
        _store = None


def new_dead_letter_id() -> str:
    return str(uuid.uuid4())