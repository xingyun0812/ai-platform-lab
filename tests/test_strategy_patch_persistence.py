"""tests/test_strategy_patch_persistence.py — StrategyPatch Postgres 持久化测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from packages.agent.self_evolve import (
    PostgresStrategyPatchStore,
    StrategyPatch,
    get_strategy_patch_store,
    reset_strategy_patch_store_for_tests,
)


class TestPostgresStrategyPatchStore(unittest.TestCase):
    def _make_mock_conn(self) -> tuple[MagicMock, MagicMock]:
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return conn, cursor

    def test_ensure_schema_creates_table(self) -> None:
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            PostgresStrategyPatchStore("postgresql://mock")
        executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("CREATE TABLE" in s and "strategy_patches" in s for s in executed_sql))

    def test_add_executes_insert(self) -> None:
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            store = PostgresStrategyPatchStore("postgresql://mock")
        strategy_patch = StrategyPatch(
            patch_id="p1",
            tenant_id="t1",
            lessons="lessons",
            proposed_change={"field": "plan_prompt", "old": "a", "new": "b"},
        )
        store.add(strategy_patch)
        last_call = cursor.execute.call_args_list[-1]
        self.assertIn("INSERT INTO strategy_patches", last_call.args[0])
        self.assertEqual(last_call.args[1][0], "p1")

    def test_approve_updates_status(self) -> None:
        conn, cursor = self._make_mock_conn()
        cursor.fetchone.return_value = {
            "patch_id": "p1",
            "tenant_id": "t1",
            "lessons": "L",
            "proposed_change_json": '{"field": "plan_prompt"}',
            "status": "pending",
            "created_at": 1000.0,
            "decided_at": None,
            "decided_by": None,
        }
        with patch("psycopg.connect", return_value=conn):
            store = PostgresStrategyPatchStore("postgresql://mock")
        ok = store.approve("p1", decided_by="reviewer")
        self.assertTrue(ok)
        last_call = cursor.execute.call_args_list[-1]
        self.assertIn("INSERT INTO strategy_patches", last_call.args[0])
        self.assertEqual(last_call.args[1][4], "approved")


class TestStrategyPatchStoreBackend(unittest.TestCase):
    def setUp(self) -> None:
        reset_strategy_patch_store_for_tests()

    def tearDown(self) -> None:
        reset_strategy_patch_store_for_tests()

    def test_database_url_selects_postgres(self) -> None:
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://mock"}):
            with patch(
                "packages.agent.self_evolve.PostgresStrategyPatchStore",
            ) as mock_pg:
                mock_pg.return_value = MagicMock()
                store = get_strategy_patch_store()
        mock_pg.assert_called_once_with("postgresql://mock")
        self.assertIs(store, mock_pg.return_value)

    def test_missing_database_url_uses_memory(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            store = get_strategy_patch_store()
        self.assertEqual(store.__class__.__name__, "StrategyPatchStore")


if __name__ == "__main__":
    unittest.main()
