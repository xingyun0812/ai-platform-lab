"""Phase O #92 — sql_query 工具单测。"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.registry import ToolRegistry, reset_tool_registry_for_tests
from packages.agent.runner import AgentRunError, run_agent
from packages.agent.session import SessionStore
from packages.agent.tools.sql_query import (
    SqlQueryForbiddenError,
    enforce_limit,
    handle_sql_query,
    mock_sql_query,
    validate_readonly_sql,
)
from packages.audit.action_levels import ActionClassifier


def _run(coro):
    return asyncio.run(coro)


class ValidateSqlTests(unittest.TestCase):
    def test_select_ok(self) -> None:
        out = validate_readonly_sql("SELECT * FROM demo_sales", max_rows=10)
        self.assertIn("LIMIT 10", out)

    def test_delete_forbidden(self) -> None:
        with self.assertRaises(SqlQueryForbiddenError):
            validate_readonly_sql("DELETE FROM demo_sales", max_rows=10)

    def test_insert_forbidden(self) -> None:
        with self.assertRaises(SqlQueryForbiddenError):
            validate_readonly_sql("INSERT INTO demo_sales VALUES (1,'x')", max_rows=10)

    def test_drop_forbidden(self) -> None:
        with self.assertRaises(SqlQueryForbiddenError):
            validate_readonly_sql("DROP TABLE demo_sales", max_rows=10)

    def test_multiple_statements_forbidden(self) -> None:
        with self.assertRaises(SqlQueryForbiddenError):
            validate_readonly_sql("SELECT 1; SELECT 2", max_rows=10)

    def test_select_into_forbidden(self) -> None:
        with self.assertRaises(SqlQueryForbiddenError):
            validate_readonly_sql("SELECT * INTO backup FROM demo_sales", max_rows=10)

    def test_existing_limit_capped(self) -> None:
        out = enforce_limit("SELECT 1 LIMIT 500", max_rows=100)
        self.assertIn("LIMIT 100", out)


class MockSqlTests(unittest.TestCase):
    def test_mock_returns_table(self) -> None:
        out = mock_sql_query("SELECT * FROM demo_sales", max_rows=3)
        self.assertEqual(out["mode"], "mock")
        self.assertEqual(out["row_count"], 3)
        self.assertEqual(len(out["columns"]), 4)

    @patch("packages.platform.get_settings")
    def test_handle_mock_mode(self, mock_settings) -> None:
        s = MagicMock()
        s.sql_query_mode = "mock"
        s.sql_agent_database_url = ""
        s.sql_query_max_rows = 100
        s.sql_query_timeout_seconds = 10.0
        mock_settings.return_value = s

        raw = _run(handle_sql_query({"sql": "SELECT region FROM demo_sales"}))
        body = json.loads(raw)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["mode"], "mock")

    @patch("packages.platform.get_settings")
    def test_handle_delete_returns_forbidden_envelope(self, mock_settings) -> None:
        mock_settings.return_value = MagicMock(
            sql_query_mode="mock",
            sql_agent_database_url="",
            sql_query_max_rows=100,
            sql_query_timeout_seconds=10.0,
        )
        raw = _run(handle_sql_query({"sql": "DELETE FROM demo_sales"}))
        body = json.loads(raw)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error_code"], "AGENT_TOOL_FORBIDDEN")


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_registry_for_tests()

    def tearDown(self) -> None:
        reset_tool_registry_for_tests()

    def test_sql_query_registered(self) -> None:
        reg = ToolRegistry()
        self.assertIsNotNone(reg.get("sql_query"))

    def test_acl_denies_unlisted(self) -> None:
        reg = ToolRegistry()
        self.assertFalse(reg.is_allowed("sql_query", ("calc",)))


class AuditTests(unittest.TestCase):
    def test_sql_query_read_only(self) -> None:
        self.assertEqual(ActionClassifier().classify("sql_query"), "read_only")


class AgentIntegrationTests(unittest.TestCase):
    @patch("packages.agent.runner.forward_with_model_router", new_callable=AsyncMock)
    @patch("packages.agent.runner.get_settings")
    def test_run_agent_select_mock(self, mock_settings, mock_route) -> None:
        from apps.gateway.settings import Settings

        mock_settings.return_value = Settings()

        calls = {"n": 0}

        async def fake_route(payload, requested_model=None):
            calls["n"] += 1
            if calls["n"] == 1:

                class R:
                    status = 200
                    body = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": "sql_query",
                                                "arguments": '{"sql":"SELECT * FROM demo_sales"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {},
                    }
                    model_used = "chat-fast"
                    error = None

                return R()

            class R2:
                status = 200
                body = {"choices": [{"message": {"role": "assistant", "content": "done"}}], "usage": {}}
                model_used = "chat-fast"
                error = None

            return R2()

        mock_route.side_effect = fake_route

        async def _go():
            return await run_agent(
                tenant_id="admin",
                session_id="sql-int",
                new_messages=[{"role": "user", "content": "query sales"}],
                allowed_tools=("sql_query",),
                allowed_models=("chat-fast",),
                model="chat-fast",
                session_store=SessionStore(),
            )

        result = _run(_go())
        trace = result.get("tool_calls") or []
        self.assertTrue(any(getattr(t, "tool_name", None) == "sql_query" for t in trace))

    @patch("packages.agent.runner.forward_with_model_router", new_callable=AsyncMock)
    @patch("packages.agent.runner.get_settings")
    def test_run_agent_delete_raises_forbidden(self, mock_settings, mock_route) -> None:
        from apps.gateway.settings import Settings

        mock_settings.return_value = Settings()

        async def fake_route(payload, requested_model=None):
            class R:
                status = 200
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "sql_query",
                                            "arguments": '{"sql":"DELETE FROM demo_sales"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {},
                }
                model_used = "chat-fast"
                error = None

            return R()

        mock_route.side_effect = fake_route

        async def _go():
            return await run_agent(
                tenant_id="admin",
                session_id="sql-bad",
                new_messages=[{"role": "user", "content": "delete all"}],
                allowed_tools=("sql_query",),
                allowed_models=("chat-fast",),
                model="chat-fast",
                session_store=SessionStore(),
            )

        with self.assertRaises(AgentRunError) as ctx:
            _run(_go())
        self.assertEqual(ctx.exception.code, "AGENT_TOOL_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
