#!/usr/bin/env python3
"""tests/test_long_horizon_persistence.py — Phase R R2+ 长程任务持久化单测。

≥8 个测试用例，无外部依赖（mock psycopg / redis）。

测试覆盖：
- TestInMemoryLongRunTaskStore: create/get/list_by_tenant/update_status/
    add_checkpoint/get_latest_checkpoint/cancel/delete（全 async）
- TestPostgresLongRunTaskStore: mock psycopg，验证 SQL 执行 /
    schema 创建 / get_latest_checkpoint ORDER BY
- TestRedisLongRunCache: mock redis client，验证 get（miss → fallback → 回填）/
    update_status（写 fallback + 失效 Redis）
- TestBackendSelection: DATABASE_URL 选 postgres / REDIS_URL 选 redis 装饰 /
    都不可达回退内存
- TestCrossProcessResume: 模拟进程 A 创建 + checkpoint → 进程 B（新 store 实例）
    get + resume 能恢复 step_states
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _ensure_namespace(name: str) -> types.ModuleType:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Bootstrap namespace packages
# ---------------------------------------------------------------------------
_ensure_namespace("packages")
_ensure_namespace("packages.contracts")
_ensure_namespace("packages.agent")

# Stub contracts.errors
_errors_mod = types.ModuleType("packages.contracts.errors")


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


_errors_mod.ErrorDetail = _ErrorDetail  # type: ignore[attr-defined]
_errors_mod.ErrorBody = _ErrorBody  # type: ignore[attr-defined]
sys.modules["packages.contracts.errors"] = _errors_mod

_agent_schemas = _load_module(
    "packages.contracts.agent_schemas",
    str(REPO_ROOT / "packages" / "contracts" / "agent_schemas.py"),
)

_long_horizon = _load_module(
    "packages.agent.long_horizon",
    str(REPO_ROOT / "packages" / "agent" / "long_horizon.py"),
)

# Import symbols
AgentPlan = _agent_schemas.AgentPlan
PlanStep = _agent_schemas.PlanStep

StepState = _long_horizon.StepState
Checkpoint = _long_horizon.Checkpoint
LongRunTask = _long_horizon.LongRunTask
LongRunTaskStore = _long_horizon.LongRunTaskStore
InMemoryLongRunTaskStore = _long_horizon.InMemoryLongRunTaskStore
PostgresLongRunTaskStore = _long_horizon.PostgresLongRunTaskStore
RedisLongRunCache = _long_horizon.RedisLongRunCache
get_long_run_store = _long_horizon.get_long_run_store
reset_long_run_store_for_tests = _long_horizon.reset_long_run_store_for_tests
new_task_id = _long_horizon.new_task_id
new_checkpoint_id = _long_horizon.new_checkpoint_id


def _run_async(coro):
    return asyncio.run(coro)


def _step(sid: str, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(id=sid, description=f"step {sid}", depends_on=depends_on or [])


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


# ---------------------------------------------------------------------------
# TestInMemoryLongRunTaskStore
# ---------------------------------------------------------------------------


class TestInMemoryLongRunTaskStore(unittest.TestCase):
    """InMemoryLongRunTaskStore 全 async 方法验证。"""

    def setUp(self) -> None:
        self.store = InMemoryLongRunTaskStore()

    def test_create_returns_task(self) -> None:
        plan = _plan(_step("s1"), _step("s2"))
        task = _run_async(self.store.create(plan, "t1", "sess1"))
        self.assertIsNotNone(task.task_id)
        self.assertEqual(task.tenant_id, "t1")
        self.assertEqual(task.session_id, "sess1")
        self.assertEqual(task.status, "pending")
        self.assertEqual(len(task.step_states), 2)

    def test_get_existing_task(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        fetched = _run_async(self.store.get(task.task_id))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.task_id, task.task_id)

    def test_get_missing_returns_none(self) -> None:
        result = _run_async(self.store.get("nonexistent-id"))
        self.assertIsNone(result)

    def test_list_by_tenant_filters_correctly(self) -> None:
        plan = _plan(_step("s1"))
        t1a = _run_async(self.store.create(plan, "tenantA"))
        t1b = _run_async(self.store.create(plan, "tenantA"))
        _run_async(self.store.create(plan, "tenantB"))

        tasks = _run_async(self.store.list_by_tenant("tenantA"))
        ids = {t.task_id for t in tasks}
        self.assertIn(t1a.task_id, ids)
        self.assertIn(t1b.task_id, ids)
        self.assertEqual(len(tasks), 2)

    def test_update_status_valid(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        ok = _run_async(self.store.update_status(task.task_id, "running"))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.status, "running")

    def test_update_status_invalid_rejected(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        ok = _run_async(self.store.update_status(task.task_id, "BOGUS"))
        self.assertFalse(ok)

    def test_add_checkpoint_and_get_latest(self) -> None:
        plan = _plan(_step("s1"), _step("s2"))
        task = _run_async(self.store.create(plan, "t1"))

        cp1 = Checkpoint(
            checkpoint_id="cp-001",
            task_id=task.task_id,
            step_states=[StepState(step_id="s1", status="completed")],
            layer_index=1,
            created_at=time.time() - 10,
        )
        cp2 = Checkpoint(
            checkpoint_id="cp-002",
            task_id=task.task_id,
            step_states=[
                StepState(step_id="s1", status="completed"),
                StepState(step_id="s2", status="completed"),
            ],
            layer_index=2,
            created_at=time.time(),
        )
        _run_async(self.store.add_checkpoint(task.task_id, cp1))
        _run_async(self.store.add_checkpoint(task.task_id, cp2))

        latest = _run_async(self.store.get_latest_checkpoint(task.task_id))
        self.assertIsNotNone(latest)
        self.assertEqual(latest.checkpoint_id, "cp-002")
        self.assertEqual(latest.layer_index, 2)

    def test_get_latest_checkpoint_no_checkpoints(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        result = _run_async(self.store.get_latest_checkpoint(task.task_id))
        self.assertIsNone(result)

    def test_cancel_pending_task(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        ok = _run_async(self.store.cancel(task.task_id))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.status, "cancelled")

    def test_cancel_completed_task_returns_false(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        _run_async(self.store.update_status(task.task_id, "completed"))
        ok = _run_async(self.store.cancel(task.task_id))
        self.assertFalse(ok)

    def test_delete_removes_task(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        ok = _run_async(self.store.delete(task.task_id))
        self.assertTrue(ok)
        self.assertIsNone(_run_async(self.store.get(task.task_id)))

    def test_set_final_result(self) -> None:
        plan = _plan(_step("s1"))
        task = _run_async(self.store.create(plan, "t1"))
        ok = _run_async(self.store.set_final_result(task.task_id, {"answer": 42}))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.final_result, {"answer": 42})


# ---------------------------------------------------------------------------
# TestPostgresLongRunTaskStore
# ---------------------------------------------------------------------------


class TestPostgresLongRunTaskStore(unittest.TestCase):
    """PostgresLongRunTaskStore — mock psycopg，验证 SQL 执行。"""

    def _make_mock_cursor(self):
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.rowcount = 1
        cursor.fetchone = MagicMock(return_value=None)
        cursor.fetchall = MagicMock(return_value=[])
        return cursor

    def _make_mock_conn(self, cursor):
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cursor)
        conn.autocommit = True
        return conn

    def _make_store(self):
        """Build a PostgresLongRunTaskStore with mocked psycopg."""
        cursor = self._make_mock_cursor()
        conn = self._make_mock_conn(cursor)

        mock_psycopg = MagicMock()
        mock_dict_row = MagicMock()
        mock_psycopg.connect = MagicMock(return_value=conn)

        mock_rows_module = MagicMock()
        mock_rows_module.dict_row = mock_dict_row

        with patch.dict(
            sys.modules,
            {"psycopg": mock_psycopg, "psycopg.rows": mock_rows_module},
        ):
            store = PostgresLongRunTaskStore("postgresql://test/db")

        return store, conn, cursor, mock_psycopg

    def test_init_creates_schema(self) -> None:
        """__init__ should call _connect + _ensure_schema (CREATE TABLE)."""
        store, conn, cursor, mock_psycopg = self._make_store()
        # Schema creation should have been called
        called_sqls = [str(c) for c in cursor.execute.call_args_list]
        schema_called = any("CREATE TABLE" in str(c) for c in cursor.execute.call_args_list)
        self.assertTrue(schema_called, "Expected CREATE TABLE to be executed")

    def test_create_executes_insert(self) -> None:
        """create() should execute an INSERT INTO long_run_tasks."""
        store, conn, cursor, _ = self._make_store()
        plan = _plan(_step("s1"))
        cursor.execute.reset_mock()

        _run_async(store.create(plan, "t1", "sess1"))

        # Check INSERT was called
        insert_called = any(
            "INSERT" in str(c) and "long_run_tasks" in str(c) for c in cursor.execute.call_args_list
        )
        self.assertTrue(insert_called, f"Expected INSERT, got: {cursor.execute.call_args_list}")

    def test_get_executes_select(self) -> None:
        """get() should execute SELECT FROM long_run_tasks WHERE task_id."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.fetchone.return_value = None

        result = _run_async(store.get("task-123"))

        select_called = any(
            "SELECT" in str(c) and "long_run_tasks" in str(c) for c in cursor.execute.call_args_list
        )
        self.assertTrue(select_called)
        self.assertIsNone(result)

    def test_get_latest_checkpoint_uses_order_by_desc(self) -> None:
        """get_latest_checkpoint() must ORDER BY created_at DESC LIMIT 1."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.fetchone.return_value = None

        _run_async(store.get_latest_checkpoint("task-abc"))

        order_by_called = any(
            "ORDER BY" in str(c) and "DESC" in str(c) and "LIMIT 1" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(
            order_by_called,
            f"Expected ORDER BY created_at DESC LIMIT 1, got: {cursor.execute.call_args_list}",
        )

    def test_add_checkpoint_executes_insert(self) -> None:
        """add_checkpoint() should INSERT INTO long_run_checkpoints."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()

        cp = Checkpoint(
            checkpoint_id="cp-test",
            task_id="task-test",
            step_states=[StepState(step_id="s1", status="completed")],
            layer_index=1,
            created_at=time.time(),
        )
        ok = _run_async(store.add_checkpoint("task-test", cp))

        insert_called = any(
            "INSERT" in str(c) and "long_run_checkpoints" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(insert_called)
        self.assertTrue(ok)

    def test_update_status_executes_update(self) -> None:
        """update_status() should UPDATE long_run_tasks SET status."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.rowcount = 1

        ok = _run_async(store.update_status("task-xyz", "running"))

        update_called = any(
            "UPDATE" in str(c) and "long_run_tasks" in str(c) and "status" in str(c)
            for c in cursor.execute.call_args_list
        )
        self.assertTrue(update_called)
        self.assertTrue(ok)

    def test_cancel_executes_conditional_update(self) -> None:
        """cancel() should UPDATE with NOT IN ('completed', 'cancelled')."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.rowcount = 1

        ok = _run_async(store.cancel("task-xyz"))

        cancel_called = any(
            "cancelled" in str(c) and "NOT IN" in str(c) for c in cursor.execute.call_args_list
        )
        self.assertTrue(
            cancel_called, f"Expected conditional cancel SQL, got: {cursor.execute.call_args_list}"
        )
        self.assertTrue(ok)

    def test_delete_executes_delete_sql(self) -> None:
        """delete() should execute DELETE FROM long_run_tasks."""
        store, conn, cursor, _ = self._make_store()
        cursor.execute.reset_mock()
        cursor.rowcount = 1

        ok = _run_async(store.delete("task-del"))

        delete_called = any(
            "DELETE" in str(c) and "long_run_tasks" in str(c) for c in cursor.execute.call_args_list
        )
        self.assertTrue(delete_called)
        self.assertTrue(ok)

    def test_row_to_task_parses_correctly(self) -> None:
        """_row_to_task should correctly reconstruct LongRunTask from DB row."""
        store, conn, cursor, _ = self._make_store()
        plan = _plan(_step("s1"), _step("s2"))
        plan_json = json.dumps(plan.model_dump())
        step_states_json = json.dumps(
            [
                {
                    "step_id": "s1",
                    "status": "completed",
                    "started_at": None,
                    "completed_at": None,
                    "sub_session_id": None,
                    "tool_calls_summary": [],
                    "error": None,
                },
                {
                    "step_id": "s2",
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "sub_session_id": None,
                    "tool_calls_summary": [],
                    "error": None,
                },
            ]
        )
        now = time.time()
        row = {
            "task_id": "test-id",
            "tenant_id": "t1",
            "session_id": "sess1",
            "plan_json": plan_json,
            "step_states_json": step_states_json,
            "status": "running",
            "created_at": now - 100,
            "updated_at": now,
            "final_result_json": None,
        }
        task = store._row_to_task(row)
        self.assertEqual(task.task_id, "test-id")
        self.assertEqual(task.status, "running")
        self.assertEqual(len(task.step_states), 2)
        self.assertEqual(task.step_states[0].status, "completed")


# ---------------------------------------------------------------------------
# TestRedisLongRunCache
# ---------------------------------------------------------------------------


class TestRedisLongRunCache(unittest.TestCase):
    """RedisLongRunCache — mock redis client，验证 cache hit/miss/invalidate。"""

    def _make_cache(self, redis_data: dict | None = None):
        """Build a RedisLongRunCache with mock redis and InMemory fallback."""
        fallback = InMemoryLongRunTaskStore()
        redis_client = MagicMock()

        if redis_data is not None:
            # Simulate cache hit
            redis_client.hgetall = MagicMock(return_value=redis_data)
        else:
            # Simulate cache miss
            redis_client.hgetall = MagicMock(return_value={})

        cache = RedisLongRunCache(redis_client, fallback)
        return cache, fallback, redis_client

    def test_get_cache_miss_falls_back_to_store(self) -> None:
        """Cache miss: hgetall returns {} → should query fallback store."""
        cache, fallback, redis_client = self._make_cache(redis_data={})

        plan = _plan(_step("s1"))
        task = _run_async(fallback.create(plan, "t1"))

        result = _run_async(cache.get(task.task_id))
        self.assertIsNotNone(result)
        self.assertEqual(result.task_id, task.task_id)
        # Should have called hgetall (cache lookup)
        redis_client.hgetall.assert_called()

    def test_get_cache_miss_fills_cache(self) -> None:
        """After cache miss, should call hset to backfill cache."""
        cache, fallback, redis_client = self._make_cache(redis_data={})

        plan = _plan(_step("s1"))
        task = _run_async(fallback.create(plan, "t1"))

        _run_async(cache.get(task.task_id))

        # hset should have been called to fill cache
        redis_client.hset.assert_called()

    def test_get_cache_hit_uses_cached_status(self) -> None:
        """Cache hit: should use status from Redis, overlay on fallback task."""
        cache, fallback, redis_client = self._make_cache()

        plan = _plan(_step("s1"))
        task = _run_async(fallback.create(plan, "t1"))

        # Simulate cache hit with different status
        step_states_json = json.dumps([s.to_dict() for s in task.step_states])
        redis_client.hgetall = MagicMock(
            return_value={
                "status": "running",
                "step_states_json": step_states_json,
                "updated_at": str(time.time()),
            }
        )

        result = _run_async(cache.get(task.task_id))
        self.assertIsNotNone(result)
        # Status from cache should override
        self.assertEqual(result.status, "running")

    def test_update_status_writes_to_store_and_invalidates_cache(self) -> None:
        """update_status should write to fallback AND call redis.delete."""
        cache, fallback, redis_client = self._make_cache(redis_data={})

        plan = _plan(_step("s1"))
        task = _run_async(fallback.create(plan, "t1"))

        ok = _run_async(cache.update_status(task.task_id, "running"))
        self.assertTrue(ok)

        # Verify fallback store was updated
        updated = _run_async(fallback.get(task.task_id))
        self.assertEqual(updated.status, "running")

        # Verify Redis key was invalidated
        redis_client.delete.assert_called()
        delete_key = redis_client.delete.call_args[0][0]
        self.assertIn(task.task_id, delete_key)

    def test_create_fills_cache(self) -> None:
        """create() should create task in store AND call hset to fill cache."""
        cache, fallback, redis_client = self._make_cache(redis_data={})
        plan = _plan(_step("s1"))

        task = _run_async(cache.create(plan, "t1", "sess1"))

        self.assertIsNotNone(task)
        redis_client.hset.assert_called()

    def test_cancel_invalidates_cache(self) -> None:
        """cancel() should invalidate Redis cache."""
        cache, fallback, redis_client = self._make_cache(redis_data={})

        plan = _plan(_step("s1"))
        task = _run_async(cache.create(plan, "t1"))
        redis_client.delete.reset_mock()

        ok = _run_async(cache.cancel(task.task_id))
        self.assertTrue(ok)
        redis_client.delete.assert_called()

    def test_redis_error_does_not_crash_get(self) -> None:
        """If Redis raises, cache.get() should still work via fallback."""
        cache, fallback, redis_client = self._make_cache()
        redis_client.hgetall = MagicMock(side_effect=Exception("redis down"))

        plan = _plan(_step("s1"))
        task = _run_async(fallback.create(plan, "t1"))

        # Should not raise
        result = _run_async(cache.get(task.task_id))
        self.assertIsNotNone(result)

    def test_list_by_tenant_delegates_to_store(self) -> None:
        """list_by_tenant() should delegate directly to fallback store."""
        cache, fallback, redis_client = self._make_cache(redis_data={})

        plan = _plan(_step("s1"))
        _run_async(cache.create(plan, "t1"))
        _run_async(cache.create(plan, "t1"))

        tasks = _run_async(cache.list_by_tenant("t1"))
        self.assertEqual(len(tasks), 2)


# ---------------------------------------------------------------------------
# TestBackendSelection
# ---------------------------------------------------------------------------


class TestBackendSelection(unittest.TestCase):
    """get_long_run_store() 自动选 backend 验证。"""

    def tearDown(self) -> None:
        reset_long_run_store_for_tests()
        # Clear env vars
        for key in ("DATABASE_URL", "REDIS_URL"):
            import os

            os.environ.pop(key, None)

    def test_no_env_vars_uses_memory(self) -> None:
        import os

        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("REDIS_URL", None)
        reset_long_run_store_for_tests()

        store = get_long_run_store()
        self.assertIsInstance(store, InMemoryLongRunTaskStore)

    def test_database_url_attempts_postgres(self) -> None:
        """With DATABASE_URL set but unreachable, should fall back to memory."""
        import os

        os.environ["DATABASE_URL"] = "postgresql://localhost/nonexistent_db_xyz"
        os.environ.pop("REDIS_URL", None)
        reset_long_run_store_for_tests()

        # Mock psycopg.connect to raise an error
        mock_psycopg = MagicMock()
        mock_psycopg.connect = MagicMock(side_effect=Exception("connection refused"))
        mock_rows = MagicMock()

        with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.rows": mock_rows}):
            store = get_long_run_store()

        # Should fall back to in-memory
        self.assertIsInstance(store, InMemoryLongRunTaskStore)

    def test_database_url_with_working_postgres_uses_postgres(self) -> None:
        """With DATABASE_URL and working psycopg, should use PostgresLongRunTaskStore."""
        import os

        os.environ["DATABASE_URL"] = "postgresql://localhost/testdb"
        os.environ.pop("REDIS_URL", None)
        reset_long_run_store_for_tests()

        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cursor)
        mock_psycopg = MagicMock()
        mock_psycopg.connect = MagicMock(return_value=conn)
        mock_rows = MagicMock()

        with patch.dict(sys.modules, {"psycopg": mock_psycopg, "psycopg.rows": mock_rows}):
            store = get_long_run_store()

        self.assertIsInstance(store, PostgresLongRunTaskStore)

    def test_redis_url_wraps_with_redis_cache(self) -> None:
        """With REDIS_URL set and working redis, should wrap store with RedisLongRunCache."""
        import os

        os.environ.pop("DATABASE_URL", None)
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        reset_long_run_store_for_tests()

        mock_redis_client = MagicMock()
        mock_redis_client.ping = MagicMock(return_value=True)

        mock_redis_module = MagicMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis_client)

        with patch.dict(sys.modules, {"redis": mock_redis_module}):
            store = get_long_run_store()

        self.assertIsInstance(store, RedisLongRunCache)

    def test_redis_unreachable_falls_back_to_base(self) -> None:
        """If Redis ping fails, should use base store without cache."""
        import os

        os.environ.pop("DATABASE_URL", None)
        os.environ["REDIS_URL"] = "redis://localhost:6379/0"
        reset_long_run_store_for_tests()

        mock_redis_client = MagicMock()
        mock_redis_client.ping = MagicMock(side_effect=Exception("redis down"))

        mock_redis_module = MagicMock()
        mock_redis_module.from_url = MagicMock(return_value=mock_redis_client)

        with patch.dict(sys.modules, {"redis": mock_redis_module}):
            store = get_long_run_store()

        self.assertIsInstance(store, InMemoryLongRunTaskStore)


# ---------------------------------------------------------------------------
# TestCrossProcessResume
# ---------------------------------------------------------------------------


class TestCrossProcessResume(unittest.TestCase):
    """跨进程 resume 验证：进程 A 创建 + checkpoint → 进程 B（新 store 实例）get + resume。"""

    def _make_mock_postgres_store(self):
        """Create a mock Postgres store that persists to an in-memory dict (simulating DB)."""
        # We simulate the DB with a shared dict
        db_tasks = {}
        db_checkpoints = {}

        # Build a real InMemoryLongRunTaskStore but expose it as if it's Postgres
        # by sharing state between two instances via a shared dict
        mem_store_a = InMemoryLongRunTaskStore()
        mem_store_b = InMemoryLongRunTaskStore()

        # Both stores share the same underlying dicts (simulating shared Postgres)
        mem_store_b._tasks = mem_store_a._tasks
        mem_store_b._tenant_index = mem_store_a._tenant_index
        mem_store_b._checkpoints = mem_store_a._checkpoints

        return mem_store_a, mem_store_b

    def test_cross_process_resume_restores_step_states(self) -> None:
        """
        Process A: create task → mark s1 completed → add checkpoint
        Process B: new store instance (shared DB) → get task → get checkpoint → resume
        After resume: step_states should match checkpoint (s1 completed, s2 pending)
        """
        store_a, store_b = self._make_mock_postgres_store()

        # === Process A ===
        plan = _plan(_step("s1"), _step("s2", ["s1"]))
        task_a = _run_async(store_a.create(plan, "t1", "sess1"))

        # Mark s1 as completed
        task_a.step_states[0].status = "completed"
        _run_async(store_a.update_step_states(task_a.task_id, task_a.step_states))
        _run_async(store_a.update_status(task_a.task_id, "paused"))

        # Add checkpoint
        cp = Checkpoint(
            checkpoint_id=new_checkpoint_id(),
            task_id=task_a.task_id,
            step_states=[
                StepState(step_id="s1", status="completed"),
                StepState(step_id="s2", status="pending"),
            ],
            layer_index=1,
            created_at=time.time(),
        )
        _run_async(store_a.add_checkpoint(task_a.task_id, cp))

        # === Process B (new store instance, shared DB) ===
        # Get task
        task_b = _run_async(store_b.get(task_a.task_id))
        self.assertIsNotNone(task_b, "Process B should find the task")
        self.assertEqual(task_b.status, "paused")

        # Get latest checkpoint
        latest_cp = _run_async(store_b.get_latest_checkpoint(task_a.task_id))
        self.assertIsNotNone(latest_cp, "Process B should find the checkpoint")
        self.assertEqual(latest_cp.layer_index, 1)

        # Resume: restore step_states from checkpoint and set status to running
        _run_async(store_b.update_step_states(task_a.task_id, list(latest_cp.step_states)))
        _run_async(store_b.update_status(task_a.task_id, "running"))

        resumed = _run_async(store_b.get(task_a.task_id))
        self.assertEqual(resumed.status, "running")
        self.assertEqual(resumed.step_states[0].status, "completed")
        self.assertEqual(resumed.step_states[1].status, "pending")

    def test_cross_process_completed_steps_tracked(self) -> None:
        """After cross-process resume, completed steps should be tracked correctly."""
        store_a, store_b = self._make_mock_postgres_store()

        plan = _plan(_step("s1"), _step("s2"), _step("s3"))
        task = _run_async(store_a.create(plan, "t1"))

        # Process A completes first 2 steps
        for i in range(2):
            task.step_states[i].status = "completed"
        _run_async(store_a.update_step_states(task.task_id, task.step_states))

        cp = Checkpoint(
            checkpoint_id=new_checkpoint_id(),
            task_id=task.task_id,
            step_states=list(task.step_states),
            layer_index=2,
            created_at=time.time(),
        )
        _run_async(store_a.add_checkpoint(task.task_id, cp))

        # Process B gets checkpoint and reconstructs completed step IDs
        latest = _run_async(store_b.get_latest_checkpoint(task.task_id))
        self.assertIsNotNone(latest)

        completed_ids = {s.step_id for s in latest.step_states if s.status == "completed"}
        self.assertEqual(completed_ids, {"s1", "s2"})
        self.assertNotIn("s3", completed_ids)

    def test_cross_process_no_checkpoint_starts_fresh(self) -> None:
        """If Process A didn't checkpoint, Process B starts from scratch."""
        store_a, store_b = self._make_mock_postgres_store()

        plan = _plan(_step("s1"))
        task = _run_async(store_a.create(plan, "t1"))

        # No checkpoint was created by Process A

        # Process B
        latest = _run_async(store_b.get_latest_checkpoint(task.task_id))
        self.assertIsNone(latest)

        # Process B resumes — all steps should be pending
        task_b = _run_async(store_b.get(task.task_id))
        completed_ids = {s.step_id for s in task_b.step_states if s.status == "completed"}
        self.assertEqual(len(completed_ids), 0)


# ---------------------------------------------------------------------------
# TestStepStateSerializationRoundtrip
# ---------------------------------------------------------------------------


class TestStepStateSerializationRoundtrip(unittest.TestCase):
    """Verify from_dict/to_dict roundtrip for StepState and Checkpoint."""

    def test_step_state_roundtrip(self) -> None:
        original = StepState(
            step_id="s1",
            status="completed",
            started_at=1000.0,
            completed_at=2000.0,
            sub_session_id="sess1__step_s1",
            tool_calls_summary=[{"tool": "calc", "result": "42"}],
            error=None,
        )
        d = original.to_dict()
        restored = StepState.from_dict(d)
        self.assertEqual(restored.step_id, original.step_id)
        self.assertEqual(restored.status, original.status)
        self.assertEqual(restored.started_at, original.started_at)
        self.assertEqual(restored.completed_at, original.completed_at)
        self.assertEqual(restored.sub_session_id, original.sub_session_id)
        self.assertEqual(restored.tool_calls_summary, original.tool_calls_summary)

    def test_checkpoint_roundtrip(self) -> None:
        ss = [
            StepState(step_id="s1", status="completed"),
            StepState(step_id="s2", status="pending"),
        ]
        now = time.time()
        original = Checkpoint(
            checkpoint_id="cp-test",
            task_id="task-test",
            step_states=ss,
            layer_index=1,
            created_at=now,
        )
        d = original.to_dict()
        restored = Checkpoint.from_dict(d)
        self.assertEqual(restored.checkpoint_id, original.checkpoint_id)
        self.assertEqual(restored.layer_index, original.layer_index)
        self.assertEqual(len(restored.step_states), 2)
        self.assertEqual(restored.step_states[0].status, "completed")

    def test_step_state_from_dict_handles_missing_fields(self) -> None:
        """from_dict should handle partial dict gracefully."""
        partial = {"step_id": "s99"}
        ss = StepState.from_dict(partial)
        self.assertEqual(ss.step_id, "s99")
        self.assertEqual(ss.status, "pending")
        self.assertIsNone(ss.error)


if __name__ == "__main__":
    unittest.main()
