from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai_platform.audit.postgres")


class AuditPostgresStore:
    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._init_schema()

    def _connect(self):
        import psycopg

        return psycopg.connect(self._url)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    tenant_id TEXT,
                    actor_role TEXT,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    latency_ms DOUBLE PRECISION,
                    trace_id TEXT,
                    model TEXT,
                    error_code TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_created
                ON audit_events (created_at DESC)
                """
            )
            conn.commit()

    def insert(
        self,
        *,
        tenant_id: str | None,
        actor_role: str | None,
        method: str,
        path: str,
        status_code: int,
        latency_ms: float,
        trace_id: str | None,
        model: str | None,
        error_code: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    tenant_id, actor_role, method, path, status_code,
                    latency_ms, trace_id, model, error_code
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    tenant_id,
                    actor_role,
                    method,
                    path,
                    status_code,
                    latency_ms,
                    trace_id,
                    model,
                    error_code,
                ),
            )
            conn.commit()

    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        limit = max(1, min(limit, 200))
        with psycopg.connect(self._url, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
