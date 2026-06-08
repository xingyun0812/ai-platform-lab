from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class AuditRecord:
    id: int
    created_at: str
    tenant_id: str | None
    method: str
    path: str
    status_code: int
    latency_ms: float
    trace_id: str | None
    model: str | None
    error_code: str | None


class AuditStore:
    """SQLite 审计表；gateway 多实例可共享同一 db 文件（WAL 模式）。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        tenant_id TEXT,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER NOT NULL,
                        latency_ms REAL NOT NULL,
                        trace_id TEXT,
                        model TEXT,
                        error_code TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC)"
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_audit_tenant
                    ON audit_events(tenant_id, created_at DESC)
                    """
                )
                conn.commit()

    def insert(
        self,
        *,
        tenant_id: str | None,
        method: str,
        path: str,
        status_code: int,
        latency_ms: float,
        trace_id: str | None = None,
        model: str | None = None,
        error_code: str | None = None,
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        created_at, tenant_id, method, path, status_code,
                        latency_ms, trace_id, model, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created_at,
                        tenant_id,
                        method,
                        path,
                        status_code,
                        round(latency_ms, 2),
                        trace_id,
                        model,
                        error_code,
                    ),
                )
                conn.commit()

    def recent(self, *, limit: int = 50, tenant_id: str | None = None) -> list[AuditRecord]:
        limit = max(1, min(limit, 500))
        with self._lock:
            with self._connect() as conn:
                if tenant_id:
                    rows = conn.execute(
                        """
                        SELECT * FROM audit_events
                        WHERE tenant_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (tenant_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM audit_events
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
        return [
            AuditRecord(
                id=row["id"],
                created_at=row["created_at"],
                tenant_id=row["tenant_id"],
                method=row["method"],
                path=row["path"],
                status_code=row["status_code"],
                latency_ms=row["latency_ms"],
                trace_id=row["trace_id"],
                model=row["model"],
                error_code=row["error_code"],
            )
            for row in rows
        ]


_store_singleton: AuditStore | None = None


def get_audit_store(db_path: Path) -> AuditStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = AuditStore(db_path)
    return _store_singleton


def reset_audit_store_for_tests() -> None:
    global _store_singleton
    _store_singleton = None
