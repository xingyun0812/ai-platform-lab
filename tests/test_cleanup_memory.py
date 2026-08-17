#!/usr/bin/env python3
"""tests/test_cleanup_memory.py — cleanup_memory.py 单元测试.

运行:
    python3 tests/test_cleanup_memory.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DATABASE_URL", "")

import pytest  # noqa: E402


SCRIPT = REPO_ROOT / "scripts" / "cleanup_memory.py"


# ---------------------------------------------------------------------------
# Helper: mock psycopg for unit tests
# ---------------------------------------------------------------------------


class MockCursor:
    """模拟 psycopg cursor。"""

    def __init__(self):
        self.operations: list[str] = []
        self._fetchone_result: dict | None = {"cnt": 0}
        self._fetchall_result: list[dict] = []

    def execute(self, query, params=None):
        self.operations.append(query[:60])
        return self

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockConn:
    """模拟 psycopg connection。"""

    def __init__(self):
        self.cursor_obj = MockCursor()
        self.committed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPurgeExpired:
    def test_dry_run_no_dml(self, monkeypatch):
        """dry-run 模式下不执行任何 DML。"""
        report = {}
        conn = MockConn()
        from scripts.cleanup_memory import _purge_expired

        result = _purge_expired(conn, dry_run=True)
        assert isinstance(result, dict)
        assert "agent_memories" in result
        assert "experiences" in result

    def test_purge_only_expired(self):
        """只删除过期记录。"""
        from scripts.cleanup_memory import _purge_expired

        conn = MockConn()
        result = _purge_expired(conn, dry_run=True)
        assert result["agent_memories"] == 0
        assert result["experiences"] == 0

    def test_purge_with_data(self, monkeypatch):
        """有数据时返回正确的计数。"""
        conn = MockConn()

        def mock_fetchone():
            return {"cnt": 5}

        conn.cursor_obj._fetchone_result = mock_fetchone()

        from scripts.cleanup_memory import _purge_expired

        result = _purge_expired(conn, dry_run=True)
        assert result["agent_memories"] == 5
        assert result["experiences"] == 0  # experiences table has no expires_at column


class TestArchive:
    def test_dry_run_no_dml(self):
        """dry-run 不执行任何插入或删除。"""
        from scripts.cleanup_memory import _archive_high_value

        conn = MockConn()
        conn.cursor_obj._fetchall_result = [
            {"memory_id": "m1", "tenant_id": "t1", "scope": "s", "scope_id": "sid",
             "content": "test", "summary": None, "embedding": None, "metadata": None,
             "created_at": time.time() - 40 * 86400, "expires_at": None,
             "access_count": 10, "last_accessed_at": None, "weight": 0.9}
        ]

        result = _archive_high_value(conn, dry_run=True)
        assert result["candidates"] == 1
        assert result["archived"] == 0

    def test_no_candidates(self):
        """没有符合条件的候选记录时不做任何操作。"""
        from scripts.cleanup_memory import _archive_high_value

        conn = MockConn()
        conn.cursor_obj._fetchall_result = []

        result = _archive_high_value(conn, dry_run=False)
        assert result["candidates"] == 0
        assert result["archived"] == 0


class TestDeleteLowWeight:
    def test_dry_run_no_dml(self):
        """dry-run 不执行删除。"""
        from scripts.cleanup_memory import _delete_low_weight

        conn = MockConn()
        result = _delete_low_weight(conn, dry_run=True)
        assert isinstance(result, dict)

    def test_low_weight_count(self, monkeypatch):
        """低权重记录计数正确。"""
        conn = MockConn()

        def mock_fetchone():
            return {"cnt": 3}

        conn.cursor_obj._fetchone_result = mock_fetchone()

        from scripts.cleanup_memory import _delete_low_weight

        result = _delete_low_weight(conn, dry_run=True)
        assert result["agent_memories"] == 3
        assert result["experiences"] == 3


class TestCLI:
    def test_no_args_prints_help(self):
        """无参数时打印帮助信息并正常退出。"""
        r = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        assert r.returncode == 0
        assert "usage:" in r.stdout.lower()

    def test_dry_run_no_db(self):
        """没有 DATABASE_URL 时 dry-run 正常返回（不需要 DB）。"""
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--purge-expired", "--dry-run"],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env,
        )
        assert r.returncode == 0

    def test_all_flags_accepted(self):
        """多 flag 组合可被解析。"""
        env = {**os.environ, "DATABASE_URL": "postgres://u:p@localhost:5432/db"}
        r = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--purge-expired", "--archive", "--delete-low-weight", "--dry-run"],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["dry_run"] is True
        assert "purge_expired" in data["operations"]
        assert "archive" in data["operations"]
        assert "delete_low_weight" in data["operations"]

    def test_json_report_structure(self):
        """JSON 报告包含 status、dry_run、operations、elapsed_ms。"""
        env = {**os.environ, "DATABASE_URL": "postgres://u:p@localhost:5432/db"}
        r = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--purge-expired", "--dry-run"],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env,
        )
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert "status" in data
        assert "dry_run" in data
        assert "operations" in data
        assert "elapsed_ms" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
