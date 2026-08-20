"""packages/agent/idempotency.py — S3: 幂等工具执行存储。

ToolExecutionRecord dataclass 定义 + IdempotencyStore ABC +
InMemoryIdempotencyStore / PostgresIdempotencyStore + 自动选 backend。"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.agent.idempotency")

__all__ = [
    "ToolExecutionRecord",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "PostgresIdempotencyStore",
    "get_idempotency_store",
    "reset_idempotency_store_for_tests",
    "make_execution_key",
    "lookup_execution",
    "record_execution",
    "clear_task_executions",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionRecord:
    """幂等执行缓存记录。"""

    execution_key: str
    task_id: str
    step: int
    tool_call_id: str
    tool_name: str
    arguments_json: str  # JSON string
    status: str
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_key": self.execution_key,
            "task_id": self.task_id,
            "step": self.step,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments_json": self.arguments_json,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolExecutionRecord:
        return cls(
            execution_key=d["execution_key"],
            task_id=d["task_id"],
            step=d["step"],
            tool_call_id=d["tool_call_id"],
            tool_name=d["tool_name"],
            arguments_json=d["arguments_json"],
            status=d["status"],
            result=d.get("result"),
            error=d.get("error"),
            created_at=d.get("created_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Abstract base store
# ---------------------------------------------------------------------------


class IdempotencyStore:
    """幂等执行存储抽象基类。"""

    def lookup(self, execution_key: str) -> ToolExecutionRecord | None:
        raise NotImplementedError

    def record(self, exec: ToolExecutionRecord) -> None:
        raise NotImplementedError

    def clear_task(self, task_id: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryIdempotencyStore
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore(IdempotencyStore):
    """线程安全的内存幂等存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, ToolExecutionRecord] = {}

    def lookup(self, execution_key: str) -> ToolExecutionRecord | None:
        with self._lock:
            return self._records.get(execution_key)

    def record(self, exec: ToolExecutionRecord) -> None:
        with self._lock:
            self._records[exec.execution_key] = exec

    def clear_task(self, task_id: str) -> None:
        with self._lock:
            keys_to_remove = [
                k for k, v in self._records.items() if v.task_id == task_id
            ]
            for k in keys_to_remove:
                self._records.pop(k, None)


# ---------------------------------------------------------------------------
# PostgresIdempotencyStore
# ---------------------------------------------------------------------------


class PostgresIdempotencyStore(IdempotencyStore):
    """Postgres 持久化幂等存储。"""

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS idempotent_tool_executions (
        execution_key TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step INTEGER NOT NULL,
        tool_call_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        error TEXT,
        created_at DOUBLE PRECISION NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_idem_task ON idempotent_tool_executions(task_id);
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

    def _row_to_record(self, row: dict[str, Any]) -> ToolExecutionRecord:
        return ToolExecutionRecord(
            execution_key=row["execution_key"],
            task_id=row["task_id"],
            step=int(row["step"]),
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            arguments_json=row["arguments_json"],
            status=row["status"],
            result=row.get("result"),
            error=row.get("error"),
            created_at=float(row["created_at"]),
        )

    def lookup(self, execution_key: str) -> ToolExecutionRecord | None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM idempotent_tool_executions WHERE execution_key = %s",
                    (execution_key,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_record(row)

    def record(self, exec: ToolExecutionRecord) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO idempotent_tool_executions
                        (execution_key, task_id, step, tool_call_id, tool_name,
                         arguments_json, status, result, error, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (execution_key) DO NOTHING
                    """,
                    (
                        exec.execution_key,
                        exec.task_id,
                        exec.step,
                        exec.tool_call_id,
                        exec.tool_name,
                        exec.arguments_json,
                        exec.status,
                        exec.result,
                        exec.error,
                        exec.created_at,
                    ),
                )

    def clear_task(self, task_id: str) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM idempotent_tool_executions WHERE task_id = %s",
                    (task_id,),
                )


# ---------------------------------------------------------------------------
# Global singleton — auto backend selection
# ---------------------------------------------------------------------------

_store: IdempotencyStore | None = None
_store_lock = threading.Lock()


def get_idempotency_store() -> IdempotencyStore:
    """自动选 backend：Postgres -> 内存兜底。"""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        database_url = os.environ.get("DATABASE_URL", "")
        if database_url:
            try:
                _store = PostgresIdempotencyStore(database_url)
                logger.info("idempotency store backend=postgres")
            except Exception as e:
                logger.warning("postgres 不可达，回退内存: %s", e)
                _store = InMemoryIdempotencyStore()
        else:
            _store = InMemoryIdempotencyStore()
            logger.info("idempotency store backend=memory")

        return _store


def reset_idempotency_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def make_execution_key(task_id: str, step: int, tool_call_id: str) -> str:
    """确定性 SHA256 哈希 hex[:32]。"""
    raw = f"{task_id}:{step}:{tool_call_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Top-level convenience functions
# ---------------------------------------------------------------------------


def lookup_execution(execution_key: str) -> ToolExecutionRecord | None:
    return get_idempotency_store().lookup(execution_key)


def record_execution(exec: ToolExecutionRecord) -> None:
    get_idempotency_store().record(exec)


def clear_task_executions(task_id: str) -> None:
    get_idempotency_store().clear_task(task_id)
