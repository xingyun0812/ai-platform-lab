from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("ai_platform.billing.store")


@dataclass(frozen=True)
class UsageRow:
    id: int
    created_at: str
    tenant_id: str
    path: str
    model: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    trace_id: str | None


class BillingStore:
    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._init_schema()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_records (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    tenant_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    trace_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_tenant_created
                ON usage_records (tenant_id, created_at DESC)
                """
            )
            conn.commit()

    def insert_usage(
        self,
        *,
        tenant_id: str,
        path: str,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        trace_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_records (
                    tenant_id, path, model,
                    input_tokens, output_tokens, total_tokens, trace_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    path,
                    model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    trace_id,
                ),
            )
            conn.commit()

    def sum_tokens(
        self,
        tenant_id: str,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> int:
        until = until or datetime.now(UTC)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS total
                FROM usage_records
                WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
                """,
                (tenant_id, since, until),
            ).fetchone()
        return int(row["total"]) if row else 0

    def aggregate_by_tenant(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        until = until or datetime.now(UTC)
        sql = """
            SELECT
                tenant_id,
                COUNT(*) AS request_count,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM usage_records
            WHERE created_at >= %s AND created_at < %s
        """
        params: list[Any] = [since, until]
        if tenant_id:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " GROUP BY tenant_id ORDER BY total_tokens DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def recent_rows(
        self,
        *,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[UsageRow]:
        limit = max(1, min(limit, 1000))
        with self._connect() as conn:
            if tenant_id:
                rows = conn.execute(
                    """
                    SELECT * FROM usage_records
                    WHERE tenant_id = %s
                    ORDER BY id DESC LIMIT %s
                    """,
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM usage_records
                    ORDER BY id DESC LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
        return [
            UsageRow(
                id=int(r["id"]),
                created_at=r["created_at"].isoformat()
                if hasattr(r["created_at"], "isoformat")
                else str(r["created_at"]),
                tenant_id=r["tenant_id"],
                path=r["path"],
                model=r.get("model"),
                input_tokens=int(r["input_tokens"]),
                output_tokens=int(r["output_tokens"]),
                total_tokens=int(r["total_tokens"]),
                trace_id=r.get("trace_id"),
            )
            for r in rows
        ]

    def export_csv(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        tenant_id: str | None = None,
    ) -> str:
        until = until or datetime.now(UTC)
        sql = """
            SELECT created_at, tenant_id, path, model,
                   input_tokens, output_tokens, total_tokens, trace_id
            FROM usage_records
            WHERE created_at >= %s AND created_at < %s
        """
        params: list[Any] = [since, until]
        if tenant_id:
            sql += " AND tenant_id = %s"
            params.append(tenant_id)
        sql += " ORDER BY id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "created_at",
                "tenant_id",
                "path",
                "model",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "trace_id",
            ]
        )
        for r in rows:
            created = r["created_at"]
            if hasattr(created, "isoformat"):
                created = created.isoformat()
            writer.writerow(
                [
                    created,
                    r["tenant_id"],
                    r["path"],
                    r.get("model") or "",
                    r["input_tokens"],
                    r["output_tokens"],
                    r["total_tokens"],
                    r.get("trace_id") or "",
                ]
            )
        return buf.getvalue()
