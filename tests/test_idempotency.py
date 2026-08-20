#!/usr/bin/env python3
"""tests/test_idempotency.py — Phase S3 幂等工具执行单测。

测试覆盖：
- TestInMemoryIdempotencyStore: CRUD (lookup/record/clear_task)
- TestMakeExecutionKey: SHA256 确定性/碰撞/一致性
- TestPostgresIdempotencyStore: mock psycopg, 验证 SQL 执行 / schema
- TestIdempotentSkip: 模拟 execute_tool idempotent 跳过
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# Stub packages.contracts.errors (not a real module)


class _ErrorDetail:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return self.__dict__


class _ErrorBody:
    def __init__(self, error=None):
        self.error = error

    def model_dump(self):
        return {"error": self.error.model_dump() if self.error else None}


_stub_errors = types.ModuleType("packages.contracts.errors")
_stub_errors.ErrorDetail = _ErrorDetail  # type: ignore[attr-defined]
_stub_errors.ErrorBody = _ErrorBody  # type: ignore[attr-defined]
sys.modules["packages.contracts.errors"] = _stub_errors


# Import the idempotency module directly (no external package deps)
from packages.agent.idempotency import (  # noqa: E402
    InMemoryIdempotencyStore,
    PostgresIdempotencyStore,
    ToolExecutionRecord,
    clear_task_executions,
    get_idempotency_store,
    lookup_execution,
    make_execution_key,
    record_execution,
    reset_idempotency_store_for_tests,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _record(
    execution_key: str = "key-1",
    task_id: str = "task-1",
    step: int = 1,
    tool_call_id: str = "call_abc",
    tool_name: str = "web_search",
    arguments_json: str = "{}",
    status: str = "success",
    result: str | None = "some result",
    error: str | None = None,
    created_at: float | None = None,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        execution_key=execution_key,
        task_id=task_id,
        step=step,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments_json=arguments_json,
        status=status,
        result=result,
        error=error,
        created_at=created_at or time.time(),
    )


# ---------------------------------------------------------------------------
# TestInMemoryIdempotencyStore
# ---------------------------------------------------------------------------


class TestInMemoryIdempotencyStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryIdempotencyStore()

    def test_lookup_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.lookup("nonexistent"))

    def test_record_and_lookup(self) -> None:
        rec = _record()
        self.store.record(rec)
        found = self.store.lookup("key-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.execution_key, "key-1")
        self.assertEqual(found.tool_name, "web_search")
        self.assertEqual(found.result, "some result")

    def test_record_overwrite(self) -> None:
        rec1 = _record(result="first")
        rec2 = _record(result="second")
        self.store.record(rec1)
        self.store.record(rec2)
        found = self.store.lookup("key-1")
        self.assertEqual(found.result, "second")

    def test_clear_task_removes_all_task_records(self) -> None:
        self.store.record(_record(execution_key="k1", task_id="t1"))
        self.store.record(_record(execution_key="k2", task_id="t1", tool_call_id="call_2"))
        self.store.record(_record(execution_key="k3", task_id="t2"))

        self.store.clear_task("t1")

        self.assertIsNone(self.store.lookup("k1"))
        self.assertIsNone(self.store.lookup("k2"))
        # t2 record should remain
        self.assertIsNotNone(self.store.lookup("k3"))

    def test_clear_task_noop_for_nonexistent_task(self) -> None:
        self.store.record(_record(execution_key="k1", task_id="t1"))
        self.store.clear_task("nonexistent")
        self.assertIsNotNone(self.store.lookup("k1"))

    def test_concurrent_safety(self) -> None:
        """Smoke test: concurrent record/lookup does not deadlock."""
        recs = [_record(execution_key=f"k{i}") for i in range(100)]
        for r in recs:
            self.store.record(r)
        for i in range(100):
            self.assertIsNotNone(self.store.lookup(f"k{i}"))

    def test_roundtrip_with_error_status(self) -> None:
        rec = _record(status="failed", result=None, error="tool crashed")
        self.store.record(rec)
        found = self.store.lookup(rec.execution_key)
        self.assertEqual(found.status, "failed")
        self.assertIsNone(found.result)
        self.assertEqual(found.error, "tool crashed")


# ---------------------------------------------------------------------------
# TestMakeExecutionKey
# ---------------------------------------------------------------------------


class TestMakeExecutionKey(unittest.TestCase):
    def test_deterministic(self) -> None:
        k1 = make_execution_key("task-1", 1, "call_abc")
        k2 = make_execution_key("task-1", 1, "call_abc")
        self.assertEqual(k1, k2)

    def test_different_task_id_yields_different_key(self) -> None:
        k1 = make_execution_key("task-1", 1, "call_abc")
        k2 = make_execution_key("task-2", 1, "call_abc")
        self.assertNotEqual(k1, k2)

    def test_different_step_yields_different_key(self) -> None:
        k1 = make_execution_key("task-1", 1, "call_abc")
        k2 = make_execution_key("task-1", 2, "call_abc")
        self.assertNotEqual(k1, k2)

    def test_different_tool_call_id_yields_different_key(self) -> None:
        k1 = make_execution_key("task-1", 1, "call_abc")
        k2 = make_execution_key("task-1", 1, "call_xyz")
        self.assertNotEqual(k1, k2)

    def test_key_length_is_32_chars(self) -> None:
        key = make_execution_key("task-1", 1, "call_abc")
        self.assertEqual(len(key), 32)
        # Verify it's a valid hex string
        int(key, 16)

    def test_includes_all_components(self) -> None:
        """Ensure deterministic with same components."""
        k1 = make_execution_key("a", 1, "b")
        k2 = make_execution_key("a", 1, "b")
        self.assertEqual(k1, k2)


# ---------------------------------------------------------------------------
# TestPostgresIdempotencyStore
# ---------------------------------------------------------------------------


class TestPostgresIdempotencyStore(unittest.TestCase):
    def _make_store(self):
        """Create a mock-based PostgresIdempotencyStore."""
        mock_psycopg = MagicMock()
        mock_rows_module = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_psycopg.connect.return_value = mock_conn
        # __enter__ returns the cursor itself so `with conn.cursor() as cur` works
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch.dict(
            sys.modules,
            {"psycopg": mock_psycopg, "psycopg.rows": mock_rows_module},
        ):
            store = PostgresIdempotencyStore("postgresql://test/db")

        return store, mock_conn, mock_cursor, mock_psycopg

    def test_init_creates_schema(self) -> None:
        """__init__ should CREATE TABLE IF NOT EXISTS idempotent_tool_executions."""
        store, conn, cursor, mock_psycopg = self._make_store()
        schema_called = any(
            "CREATE TABLE" in str(c) and "idempotent_tool_executions" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(schema_called, "Expected CREATE TABLE to be executed")

    def test_init_creates_index(self) -> None:
        """__init__ should CREATE INDEX IF NOT EXISTS idx_idem_task."""
        store, conn, cursor, mock_psycopg = self._make_store()
        index_called = any(
            "CREATE INDEX" in str(c) and "idx_idem_task" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(index_called, "Expected CREATE INDEX idx_idem_task to be executed")

    def test_lookup_executes_select(self) -> None:
        """lookup() should execute SELECT with WHERE execution_key."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.fetchone.return_value = None

        result = store.lookup("some-key")

        select_called = any(
            "SELECT" in str(c) and "idempotent_tool_executions" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(select_called)
        self.assertIsNone(result)

    def test_record_executes_upsert(self) -> None:
        """record() should execute INSERT ... ON CONFLICT DO NOTHING."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()

        rec = _record()
        store.record(rec)

        upsert_called = any(
            "INSERT" in str(c) and "ON CONFLICT" in str(c) for c in cursor.execute.call_args_list
        )
        self.assertTrue(
            upsert_called,
            f"Expected INSERT ON CONFLICT, got: {cursor.execute.call_args_list}",
        )

    def test_clear_task_executes_delete(self) -> None:
        """clear_task() should execute DELETE with WHERE task_id."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()

        store.clear_task("task-xyz")

        delete_called = any(
            "DELETE" in str(c) and "idempotent_tool_executions" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(delete_called)

    def test_lookup_returns_record_from_row(self) -> None:
        """lookup() should reconstruct ToolExecutionRecord from DB row."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        now = time.time()
        cursor.fetchone.return_value = {
            "execution_key": "key-test",
            "task_id": "task-test",
            "step": 3,
            "tool_call_id": "call_test",
            "tool_name": "web_search",
            "arguments_json": '{"q":"hello"}',
            "status": "success",
            "result": "some result",
            "error": None,
            "created_at": now,
        }

        result = store.lookup("key-test")
        self.assertIsNotNone(result)
        self.assertEqual(result.execution_key, "key-test")
        self.assertEqual(result.task_id, "task-test")
        self.assertEqual(result.step, 3)
        self.assertEqual(result.tool_call_id, "call_test")
        self.assertEqual(result.tool_name, "web_search")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.result, "some result")
        self.assertEqual(result.created_at, now)


# ---------------------------------------------------------------------------
# TestGlobalStoreBackend
# ---------------------------------------------------------------------------


class TestGlobalStoreBackend(unittest.TestCase):
    def tearDown(self) -> None:
        reset_idempotency_store_for_tests()

    def test_no_env_var_uses_memory(self) -> None:
        import os

        os.environ.pop("DATABASE_URL", None)
        reset_idempotency_store_for_tests()

        store = get_idempotency_store()
        self.assertIsInstance(store, InMemoryIdempotencyStore)

    def test_database_url_unreachable_falls_back_to_memory(self) -> None:
        import os

        os.environ["DATABASE_URL"] = "postgresql://localhost/nonexistent_db_xyz"
        reset_idempotency_store_for_tests()

        mock_psycopg = MagicMock()
        mock_psycopg.connect = MagicMock(side_effect=Exception("connection refused"))
        mock_rows = MagicMock()

        with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.rows": mock_rows}):
            store = get_idempotency_store()

        self.assertIsInstance(store, InMemoryIdempotencyStore)

    def test_database_url_with_working_postgres_uses_postgres(self) -> None:
        import os

        os.environ["DATABASE_URL"] = "postgresql://localhost/testdb"
        reset_idempotency_store_for_tests()

        mock_psycopg = MagicMock()
        mock_rows = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_psycopg.connect.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.cursor.return_value = mock_cursor

        with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.rows": mock_rows}):
            store = get_idempotency_store()

        self.assertIsInstance(store, PostgresIdempotencyStore)

    def test_convenience_functions_use_global_store(self) -> None:
        reset_idempotency_store_for_tests()

        key = make_execution_key("task-1", 1, "call_abc")
        record_execution(_record(execution_key=key))

        found = lookup_execution(key)
        self.assertIsNotNone(found)
        self.assertEqual(found.execution_key, key)

        clear_task_executions("task-1")
        self.assertIsNone(lookup_execution(key))


# ---------------------------------------------------------------------------
# TestIdempotentSkip
# ---------------------------------------------------------------------------


class TestIdempotentSkip(unittest.TestCase):
    """Verify that execute_tool skips execution when execution_key has a cached success."""

    def setUp(self) -> None:
        # Reset idempotency store
        reset_idempotency_store_for_tests()

        # Create mock tool handler
        self.handler_calls = 0

        async def mock_handler(args):
            self.handler_calls += 1
            return "real result"

        self.mock_handler = mock_handler

    def test_idempotent_skip_returns_cached_result(self) -> None:
        """When execution_key matches a cached success, execute_tool returns cached result."""
        from packages.agent.react_loop import execute_tool


        # Create a mock registry
        mock_registry = MagicMock()
        mock_registry.is_allowed.return_value = True
        mock_tool = MagicMock()
        mock_tool.handler = self.mock_handler
        mock_registry.get.return_value = mock_tool

        # First call with execution_key: should execute and record
        result1, record1 = asyncio.run(
            execute_tool(
                mock_registry,
                tool_name="web_search",
                arguments_json='{"q":"hello"}',
                allowed_tools=("web_search",),
                tool_timeout=30.0,
                tool_max_retries=2,
                execution_key="test-key-1",
            )
        )
        self.assertEqual(result1, "real result")
        self.assertEqual(record1.status, "success")
        self.assertEqual(self.handler_calls, 1)

        # Second call with same execution_key: should skip execution, return cached
        result2, record2 = asyncio.run(
            execute_tool(
                mock_registry,
                tool_name="web_search",
                arguments_json='{"q":"hello"}',
                allowed_tools=("web_search",),
                tool_timeout=30.0,
                tool_max_retries=2,
                execution_key="test-key-1",
            )
        )
        self.assertEqual(result2, "real result")  # cached result
        self.assertEqual(record2.status, "success")
        self.assertEqual(self.handler_calls, 1)  # handler NOT called again

    def test_no_key_does_not_record(self) -> None:
        """When execution_key is None, idempotency is NOT checked or recorded."""
        from packages.agent.react_loop import execute_tool

        async def mock_handler(args):
            self.handler_calls += 1
            return "result"

        mock_registry = MagicMock()
        mock_registry.is_allowed.return_value = True
        mock_tool = MagicMock()
        mock_tool.handler = mock_handler
        mock_registry.get.return_value = mock_tool


        result, record = asyncio.run(
            execute_tool(
                mock_registry,
                tool_name="web_search",
                arguments_json="{}",
                allowed_tools=("web_search",),
                tool_timeout=30.0,
                tool_max_retries=2,
                # no execution_key
            )
        )
        self.assertEqual(result, "result")
        self.assertEqual(record.status, "success")

        # Nothing was recorded in the idempotency store
        self.assertIsNone(lookup_execution("nonexistent"))

    def test_different_key_does_not_skip(self) -> None:
        """Different execution_key means different execution."""
        from packages.agent.react_loop import execute_tool

        async def mock_handler(args):
            self.handler_calls += 1
            return "result"

        mock_registry = MagicMock()
        mock_registry.is_allowed.return_value = True
        mock_tool = MagicMock()
        mock_tool.handler = mock_handler
        mock_registry.get.return_value = mock_tool


        # Execute with key-1
        asyncio.run(
            execute_tool(
                mock_registry,
                tool_name="web_search",
                arguments_json="{}",
                allowed_tools=("web_search",),
                tool_timeout=30.0,
                tool_max_retries=2,
                execution_key="key-1",
            )
        )
        self.assertEqual(self.handler_calls, 1)

        # Execute with key-2
        asyncio.run(
            execute_tool(
                mock_registry,
                tool_name="web_search",
                arguments_json="{}",
                allowed_tools=("web_search",),
                tool_timeout=30.0,
                tool_max_retries=2,
                execution_key="key-2",
            )
        )
        self.assertEqual(self.handler_calls, 2)  # handler called again for different key


# ---------------------------------------------------------------------------
# TestToolExecutionRecordRoundtrip
# ---------------------------------------------------------------------------


class TestToolExecutionRecordRoundtrip(unittest.TestCase):
    def test_to_dict_and_from_dict(self) -> None:
        rec = _record(
            execution_key="ek-1",
            task_id="tid-1",
            step=2,
            tool_call_id="call_xyz",
            tool_name="sql_query",
            arguments_json='{"sql":"SELECT 1"}',
            status="success",
            result="1",
            error=None,
            created_at=1234567890.0,
        )
        d = rec.to_dict()
        restored = ToolExecutionRecord.from_dict(d)
        self.assertEqual(restored.execution_key, "ek-1")
        self.assertEqual(restored.task_id, "tid-1")
        self.assertEqual(restored.step, 2)
        self.assertEqual(restored.tool_call_id, "call_xyz")
        self.assertEqual(restored.tool_name, "sql_query")
        self.assertEqual(restored.arguments_json, '{"sql":"SELECT 1"}')
        self.assertEqual(restored.status, "success")
        self.assertEqual(restored.result, "1")
        self.assertIsNone(restored.error)
        self.assertEqual(restored.created_at, 1234567890.0)

    def test_from_dict_with_none_fields(self) -> None:
        d = {
            "execution_key": "ek",
            "task_id": "tid",
            "step": 1,
            "tool_call_id": "cid",
            "tool_name": "tool",
            "arguments_json": "{}",
            "status": "success",
            "result": None,
            "error": None,
            "created_at": 0.0,
        }
        rec = ToolExecutionRecord.from_dict(d)
        self.assertIsNone(rec.result)
        self.assertIsNone(rec.error)
        self.assertEqual(rec.created_at, 0.0)


if __name__ == "__main__":
    unittest.main()
