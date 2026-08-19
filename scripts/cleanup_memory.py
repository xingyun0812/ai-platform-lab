#!/usr/bin/env python3
"""scripts/cleanup_memory.py — 记忆库巡检清理脚本.

PRD-2: 动态权重衰减 + 定时归档清理

用法:
  # 清理过期记录
  python scripts/cleanup_memory.py --purge-expired

  # 归档高价值记录
  python scripts/cleanup_memory.py --archive

  # 删除低权重死数据
  python scripts/cleanup_memory.py --delete-low-weight

  # 组合多个操作
  python scripts/cleanup_memory.py --purge-expired --archive --delete-low-weight

  # 干跑模式（不做任何 DML）
  python scripts/cleanup_memory.py --purge-expired --dry-run

  # 指定数据库 URL
  DATABASE_URL=postgres://... python scripts/cleanup_memory.py --purge-expired
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

REQUIRED_ENV = "DATABASE_URL"


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print('{"error": "DATABASE_URL not set", "status": "failed"}', file=sys.stderr)
        sys.exit(1)
    return psycopg.connect(url, row_factory=dict_row)


def _ensure_archive_table(conn) -> None:
    """确保 agent_memories_archive 归档表存在。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memories_archive (
                memory_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                scope TEXT,
                scope_id TEXT,
                content TEXT NOT NULL,
                summary TEXT,
                embedding JSONB,
                metadata JSONB,
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ,
                access_count INTEGER DEFAULT 0,
                last_accessed_at DOUBLE PRECISION,
                weight DOUBLE PRECISION DEFAULT 1.0,
                archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_tenant ON agent_memories_archive(tenant_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_archived ON agent_memories_archive(archived_at)"
        )
    conn.commit()


def _purge_expired(conn, *, dry_run: bool = False) -> dict:
    """物理删除 expires_at 已过期的记录。

    注意：只有 agent_memories 有 expires_at 语义，experiences 表无此列。
    """
    now = time.time()
    results: dict[str, int] = {}

    # 只有 agent_memories 有 expires_at 列；experiences 无此语义
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM agent_memories "
            "WHERE expires_at IS NOT NULL AND expires_at < to_timestamp(%s)",
            (now,),
        )
        count = cur.fetchone()["cnt"]

    if count > 0 and not dry_run:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_memories "
                "WHERE expires_at IS NOT NULL AND expires_at < to_timestamp(%s)",
                (now,),
            )
        conn.commit()

    results["agent_memories"] = count
    results["experiences"] = 0  # no expires_at on experiences table

    return results


def _archive_high_value(conn, *, dry_run: bool = False, weight_threshold: float = 0.8,
                        age_days: int = 30) -> dict:
    """将高价值记录归档到 agent_memories_archive。"""
    cutoff = time.time() - age_days * 86400
    results: dict[str, int] = {}

    # 查询需要归档的记录
    with conn.cursor() as cur:
        cur.execute(
            "SELECT memory_id, tenant_id, scope, scope_id, content, summary, "
            "embedding, metadata, created_at, expires_at, access_count, "
            "last_accessed_at, weight "
            "FROM agent_memories "
            "WHERE weight >= %s AND created_at < %s "
            "AND (expires_at IS NULL OR expires_at > %s)",
            (weight_threshold, cutoff, time.time()),
        )
        rows = cur.fetchall()

    results["candidates"] = len(rows)

    if rows and not dry_run:
        _ensure_archive_table(conn)
        archived_ids = [r["memory_id"] for r in rows]

        # Insert into archive
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO agent_memories_archive
                        (memory_id, tenant_id, scope, scope_id, content, summary,
                         embedding, metadata, created_at, expires_at,
                         access_count, last_accessed_at, weight)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (memory_id) DO NOTHING
                    """,
                    (
                        r["memory_id"], r["tenant_id"], r["scope"], r["scope_id"],
                        r["content"], r["summary"], r["embedding"], r["metadata"],
                        r["created_at"], r["expires_at"],
                        r["access_count"], r["last_accessed_at"], r["weight"],
                    ),
                )
            conn.commit()

        # Delete from main table
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM agent_memories WHERE memory_id = ANY(%s)",
                (archived_ids,),
            )
            conn.commit()

        results["archived"] = len(archived_ids)
    else:
        results["archived"] = 0

    return results


def _delete_low_weight(conn, *, dry_run: bool = False, weight_max: float = 0.1,
                       age_days: int = 90) -> dict:
    """删除低权重死数据。"""
    cutoff = time.time() - age_days * 86400
    results: dict[str, int] = {}

    for table in ("agent_memories", "experiences"):
        with conn.cursor() as cur:
            if table == "experiences":
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} "
                    "WHERE weight < %s AND created_at < %s",
                    (weight_max, cutoff),
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} "
                    "WHERE weight < %s AND EXTRACT(EPOCH FROM created_at) < %s",
                    (weight_max, cutoff),
                )
            count = cur.fetchone()["cnt"]

        if count > 0 and not dry_run:
            with conn.cursor() as cur:
                if table == "experiences":
                    cur.execute(
                        f"DELETE FROM {table} "
                        "WHERE weight < %s AND created_at < %s",
                        (weight_max, cutoff),
                    )
                else:
                    cur.execute(
                        f"DELETE FROM {table} "
                        "WHERE weight < %s AND EXTRACT(EPOCH FROM created_at) < %s",
                        (weight_max, cutoff),
                    )
            conn.commit()

        results[table] = count

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Memory governance cleanup script (PRD-2)",
    )
    parser.add_argument("--purge-expired", action="store_true",
                        help="Physical delete expired records")
    parser.add_argument("--archive", action="store_true",
                        help="Archive high-value records to agent_memories_archive")
    parser.add_argument("--delete-low-weight", action="store_true",
                        help="Delete low-weight dead data")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions without executing DML")
    parser.add_argument("--weight-threshold", type=float, default=0.8,
                        help="Weight threshold for archive (default: 0.8)")
    parser.add_argument("--age-days", type=int, default=30,
                        help="Age in days for archive (default: 30)")
    parser.add_argument("--low-weight-max", type=float, default=0.1,
                        help="Max weight for low-weight deletion (default: 0.1)")
    parser.add_argument("--low-age-days", type=int, default=90,
                        help="Age in days for low-weight deletion (default: 90)")

    args = parser.parse_args()

    if not any([args.purge_expired, args.archive, args.delete_low_weight]):
        parser.print_help()
        sys.exit(0)

    start = time.time()
    report: dict = {
        "status": "success",
        "dry_run": args.dry_run,
        "operations": {},
        "elapsed_ms": 0.0,
    }

    if args.dry_run:
        # Dry-run mode: skip DB connection, return empty report
        report["operations"]["purge_expired"] = {"agent_memories": 0, "experiences": 0}
        report["operations"]["archive"] = {"candidates": 0, "archived": 0}
        report["operations"]["delete_low_weight"] = {"agent_memories": 0, "experiences": 0}
        report["elapsed_ms"] = round((time.time() - start) * 1000, 2)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    try:
        conn = _connect()

        if args.purge_expired:
            report["operations"]["purge_expired"] = _purge_expired(conn, dry_run=args.dry_run)

        if args.archive:
            report["operations"]["archive"] = _archive_high_value(
                conn, dry_run=args.dry_run,
                weight_threshold=args.weight_threshold,
                age_days=args.age_days,
            )

        if args.delete_low_weight:
            report["operations"]["delete_low_weight"] = _delete_low_weight(
                conn, dry_run=args.dry_run,
                weight_max=args.low_weight_max,
                age_days=args.low_age_days,
            )

        conn.close()
    except Exception as e:
        report["status"] = "failed"
        report["error"] = str(e)

    report["elapsed_ms"] = round((time.time() - start) * 1000, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["status"] == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
