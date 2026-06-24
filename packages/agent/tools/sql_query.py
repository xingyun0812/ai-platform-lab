"""Agent 工具 sql_query — Phase O #92

只读 SELECT 沙箱：拒绝 DML/DDL，强制 LIMIT，支持 mock / Postgres。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from packages.agent.tool_envelope import failure_envelope, success_envelope

logger = logging.getLogger("ai_platform.agent.tools.sql_query")

_FORBIDDEN_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"COPY|EXEC|EXECUTE|CALL|MERGE|REPLACE|ATTACH|DETACH|VACUUM|ANALYZE|"
    r"REINDEX|CLUSTER|COMMENT|SECURITY|LOAD|REFRESH"
    r")\b",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_MOCK_SALES_ROWS = [
    {"region": "CN", "product": "Widget A", "amount": 12000.0, "quarter": "2024-Q1"},
    {"region": "CN", "product": "Widget B", "amount": 8500.0, "quarter": "2024-Q1"},
    {"region": "US", "product": "Widget A", "amount": 15200.0, "quarter": "2024-Q1"},
    {"region": "EU", "product": "Widget C", "amount": 6300.0, "quarter": "2024-Q2"},
    {"region": "CN", "product": "Widget A", "amount": 14100.0, "quarter": "2024-Q2"},
]


class SqlQueryForbiddenError(Exception):
    """只读 SQL 校验失败（应对应 AGENT_TOOL_FORBIDDEN）。"""


class SqlQueryError(Exception):
    """SQL 执行或参数错误。"""


def validate_readonly_sql(sql: str, *, max_rows: int) -> str:
    """校验并规范化 SQL；非法时抛 SqlQueryForbiddenError。"""
    if not isinstance(sql, str) or not sql.strip():
        raise SqlQueryError("sql 不能为空")

    text = sql.strip()
    parts = [p.strip() for p in text.split(";") if p.strip()]
    if len(parts) > 1:
        raise SqlQueryForbiddenError("不允许多语句")
    text = parts[0] if parts else text.rstrip(";").strip()

    upper = text.upper()
    if not upper.startswith("SELECT"):
        raise SqlQueryForbiddenError("仅允许 SELECT 查询")

    if _FORBIDDEN_KEYWORDS.search(text):
        raise SqlQueryForbiddenError("检测到禁止的写操作或 DDL 关键字")

    if re.search(r"\bSELECT\b.*\bINTO\b", text, re.IGNORECASE):
        raise SqlQueryForbiddenError("不允许 SELECT INTO")

    if re.search(r"\bFOR\s+UPDATE\b", text, re.IGNORECASE):
        raise SqlQueryForbiddenError("不允许 FOR UPDATE")

    return enforce_limit(text, max_rows=max_rows)


def enforce_limit(sql: str, *, max_rows: int) -> str:
    match = _LIMIT_RE.search(sql)
    if match:
        try:
            n = int(match.group(1))
        except ValueError:
            n = max_rows
        if n > max_rows:
            return _LIMIT_RE.sub(f"LIMIT {max_rows}", sql, count=1)
        return sql
    return f"{sql} LIMIT {max_rows}"


def mock_sql_query(sql: str, *, max_rows: int) -> dict[str, Any]:
    safe = validate_readonly_sql(sql, max_rows=max_rows)
    rows = _MOCK_SALES_ROWS[:max_rows]
    return {
        "mode": "mock",
        "sql": safe,
        "columns": ["region", "product", "amount", "quarter"],
        "rows": [[r["region"], r["product"], r["amount"], r["quarter"]] for r in rows],
        "row_count": len(rows),
    }


async def postgres_sql_query(
    sql: str,
    *,
    database_url: str,
    max_rows: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    safe = validate_readonly_sql(sql, max_rows=max_rows)

    import psycopg

    async def _run() -> dict[str, Any]:
        async with await psycopg.AsyncConnection.connect(database_url) as conn:
            conn.read_only = True
            async with conn.cursor() as cur:
                await cur.execute(safe)
                if cur.description is None:
                    return {
                        "mode": "postgres",
                        "sql": safe,
                        "columns": [],
                        "rows": [],
                        "row_count": 0,
                    }
                columns = [d.name for d in cur.description]
                fetched = await cur.fetchall()
                rows = [list(row) for row in fetched]
                return {
                    "mode": "postgres",
                    "sql": safe,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                }

    return await asyncio.wait_for(_run(), timeout=timeout_seconds)


async def handle_sql_query(arguments: dict[str, Any]) -> str:
    sql = arguments.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return json.dumps({"error": "sql 不能为空"}, ensure_ascii=False)

    from apps.gateway.settings import get_settings

    settings = get_settings()
    max_rows = max(1, min(int(getattr(settings, "sql_query_max_rows", 100) or 100), 500))
    timeout = float(getattr(settings, "sql_query_timeout_seconds", 10.0) or 10.0)
    mode = (getattr(settings, "sql_query_mode", "mock") or "mock").strip().lower()
    db_url = (getattr(settings, "sql_agent_database_url", "") or "").strip()

    try:
        if mode == "postgres" and db_url:
            payload = await postgres_sql_query(
                sql,
                database_url=db_url,
                max_rows=max_rows,
                timeout_seconds=timeout,
            )
        else:
            payload = mock_sql_query(sql, max_rows=max_rows)
    except SqlQueryForbiddenError as e:
        return failure_envelope(error_code="AGENT_TOOL_FORBIDDEN", message=str(e))
    except SqlQueryError as e:
        return failure_envelope(error_code="SQL_QUERY_INVALID", message=str(e))
    except TimeoutError:
        return failure_envelope(error_code="SQL_QUERY_TIMEOUT", message="查询超时")
    except Exception as e:
        logger.warning("sql_query failed: %s", e)
        if mode == "postgres" and db_url:
            payload = mock_sql_query(sql, max_rows=max_rows)
            payload["mode"] = "mock_fallback"
        else:
            return failure_envelope(error_code="SQL_QUERY_ERROR", message=str(e))

    return success_envelope({"tool": "sql_query", **payload})
