"""Phase O #89 — Multi-Agent v2 黑板 + Runner 委托单测。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent.multi_agent.blackboard import (
    InMemoryBlackboardStore,
    format_entries_for_reviewer,
    reset_blackboard_for_tests,
)
from packages.agent.multi_agent.delegation import (
    resolve_delegation_tools,
)
from packages.agent.multi_agent.registry import (
    AgentRegistry,
    AgentSpec,
    reset_agent_registry_for_tests,
)


def _run(coro):
    return asyncio.run(coro)


class BlackboardStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_blackboard_for_tests()
        self.bb = InMemoryBlackboardStore()

    def test_append_and_list(self) -> None:
        e1 = self.bb.append("admin", "sess-1", agent_id="a1", role="specialist", content="hello")
        self.bb.append("admin", "sess-1", agent_id="a2", role="reviewer", content="ok")
        items = self.bb.list_entries("admin", "sess-1")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].entry_id, e1.entry_id)
        self.assertEqual(items[1].content, "ok")

    def test_list_limit(self) -> None:
        for i in range(5):
            self.bb.append("admin", "s", agent_id="a", role="specialist", content=str(i))
        items = self.bb.list_entries("admin", "s", limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[-1].content, "4")

    def test_clear(self) -> None:
        self.bb.append("admin", "s", agent_id="a", role="specialist", content="x")
        self.bb.clear("admin", "s")
        self.assertEqual(self.bb.list_entries("admin", "s"), [])

    def test_tenant_isolation(self) -> None:
        self.bb.append("t1", "s", agent_id="a", role="specialist", content="one")
        self.bb.append("t2", "s", agent_id="a", role="specialist", content="two")
        self.assertEqual(len(self.bb.list_entries("t1", "s")), 1)
        self.assertEqual(self.bb.list_entries("t2", "s")[0].content, "two")

    def test_format_for_reviewer(self) -> None:
        self.bb.append("admin", "s", agent_id="rag", role="specialist", content="检索结果")
        text = format_entries_for_reviewer(self.bb.list_entries("admin", "s"))
        self.assertIn("[specialist:rag]", text)
        self.assertIn("检索结果", text)


class ResolveDelegationToolsTests(unittest.TestCase):
    def test_spec_whitelist_intersect_tenant(self) -> None:
        out = resolve_delegation_tools(["calc", "get_kb_snippet"], ("calc",))
        self.assertEqual(out, ("calc",))

    def test_empty_spec_uses_tenant(self) -> None:
        out = resolve_delegation_tools([], ("calc", "get_kb_snippet"))
        self.assertEqual(out, ("calc", "get_kb_snippet"))

    def test_no_tenant_uses_spec(self) -> None:
        out = resolve_delegation_tools(["calc"], None)
        self.assertEqual(out, ("calc",))


class DelegationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_agent_registry_for_tests()
        reset_blackboard_for_tests()
        from packages.agent.multi_agent import registry as reg_mod

        reg = AgentRegistry()
        reg.add_agent(
            AgentSpec(
                agent_id="worker",
                name="Worker",
                role="specialist",
                system_prompt="你是专家",
                allowed_tools=["calc"],
            )
        )
        reg.add_agent(
            AgentSpec(
                agent_id="reviewer",
                name="Reviewer",
                role="reviewer",
                system_prompt="你是审核员",
            )
        )
        reg_mod._global_registry = reg

    def tearDown(self) -> None:
        reset_agent_registry_for_tests()

    @patch("packages.agent.runner.run_agent", new_callable=AsyncMock)
    def test_delegate_via_runner_writes_blackboard(self, mock_run) -> None:
        mock_run.return_value = {
            "status": "completed",
            "final_message": "42",
            "tool_calls": [{"tool_name": "calc"}],
            "_platform": {"usage": {"total_tokens": 10}},
        }
        from packages.agent.multi_agent.delegation import delegate_to_agent

        bb = InMemoryBlackboardStore()

        async def _go():
            return await delegate_to_agent(
                agent_id="worker",
                task="计算 6*7",
                tenant_id="admin",
                session_id="vertical-1",
                blackboard=bb,
            )

        result = _run(_go())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "42")
        self.assertIsNotNone(result.blackboard_entry_id)
        entries = bb.list_entries("admin", "vertical-1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].agent_id, "worker")
        mock_run.assert_awaited_once()

    @patch("packages.agent.runner.run_agent", new_callable=AsyncMock)
    def test_reviewer_reads_blackboard(self, mock_run) -> None:
        bb = InMemoryBlackboardStore()
        bb.append("admin", "sess-r", agent_id="worker", role="specialist", content="draft answer")

        mock_run.return_value = {
            "status": "completed",
            "final_message": "approved",
            "tool_calls": [],
            "_platform": {},
        }
        from packages.agent.multi_agent.delegation import delegate_to_agent

        async def _go():
            return await delegate_to_agent(
                agent_id="reviewer",
                task="审核上文",
                tenant_id="admin",
                session_id="sess-r",
                blackboard=bb,
            )

        _run(_go())
        call_kwargs = mock_run.await_args.kwargs
        msgs = call_kwargs["new_messages"]
        system = next(m for m in msgs if m["role"] == "system")
        self.assertIn("draft answer", system["content"])

    @patch("packages.agent.runner.run_agent", new_callable=AsyncMock)
    def test_delegate_run_agent_error(self, mock_run) -> None:
        from packages.agent.multi_agent.delegation import delegate_to_agent
        from packages.agent.runner import AgentRunError

        mock_run.side_effect = AgentRunError("MODEL_NOT_ALLOWED", "model blocked")

        async def _go():
            return await delegate_to_agent(agent_id="worker", task="x", session_id="s1")

        result = _run(_go())
        self.assertEqual(result.status, "failed")
        self.assertIn("MODEL_NOT_ALLOWED", result.error or "")

    def test_delegate_cycle_still_blocks_before_runner(self) -> None:
        from packages.agent.multi_agent.delegation import delegate_to_agent

        async def _go():
            return await delegate_to_agent(
                agent_id="worker",
                task="x",
                delegation_stack=["worker"],
            )

        result = _run(_go())
        self.assertIn("DELEGATION_CYCLE", result.error or "")


class BlackboardApiTests(unittest.TestCase):
    def test_get_blackboard_route(self) -> None:
        from fastapi.testclient import TestClient

        from apps.gateway.main import app

        reset_blackboard_for_tests()
        bb = InMemoryBlackboardStore()
        bb.append("admin", "api-sess", agent_id="worker", role="specialist", content="hi")

        with patch("apps.gateway.agent.routes.get_blackboard", return_value=bb):
            client = TestClient(app)
            r = client.get(
                "/v1/agent/blackboard/api-sess",
                headers={
                    "X-Tenant-Id": "admin",
                    "Authorization": "Bearer sk-tenant-admin-change-me",
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["session_id"], "api-sess")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["entries"][0]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
