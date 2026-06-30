"""tests/test_experience_run_store_plan_e2e.py — Phase R run→store→plan 全链 (#187)。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent.experience_store import (
    compute_task_signature,
    get_experience_store,
    reset_experience_store_for_tests,
)
from packages.agent.self_evolve import reset_strategy_patch_store_for_tests
from packages.contracts.agent_schemas import AgentPlan, PlanStep
from tests.gateway_client import LifespanTestClient

_GOAL = "汇总 Q3 渠道销售数据"
_LESSON_MARKER = "RUN_E2E_LESSON_FROM_REFLECT"
_PLAN_JSON = (
    '{"goal":"汇总 Q3 渠道销售数据","steps":[{"id":"s1","description":"汇总","depends_on":[]}]}'
)
_SAMPLE_PLAN = AgentPlan(
    goal=_GOAL,
    steps=[PlanStep(id="s1", description="汇总", depends_on=[])],
)

_ADMIN_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}


class TestExperienceRunStorePlanE2E(unittest.TestCase):
    """POST run 终态写经验 → 同 goal plan 注入 lessons。"""

    def setUp(self) -> None:
        reset_experience_store_for_tests()
        reset_strategy_patch_store_for_tests()
        self._client_cm = LifespanTestClient()
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        reset_experience_store_for_tests()
        reset_strategy_patch_store_for_tests()

    @patch("packages.agent.experience_store.compute_task_embedding", new_callable=AsyncMock, return_value=None)
    @patch("packages.agent.self_evolve.reflect_on_run", new_callable=AsyncMock, return_value=_LESSON_MARKER)
    @patch("packages.agent.self_evolve.maybe_patch_strategy", new_callable=AsyncMock, return_value=None)
    @patch("apps.gateway.agent.routes.execute_agent_graph", new_callable=AsyncMock)
    def test_run_stores_experience_then_plan_injects(
        self,
        mock_graph: AsyncMock,
        _mock_patch: AsyncMock,
        _mock_reflect: AsyncMock,
        _mock_emb: AsyncMock,
    ) -> None:
        from packages.agent.run_lifecycle import finalize_agent_run_result

        async def fake_graph(**kwargs):
            result = {
                "tenant_id": "admin",
                "session_id": "run-e2e-session",
                "final_message": "done",
                "tool_calls": [
                    {
                        "tool_name": "calc",
                        "arguments": {},
                        "status": "success",
                        "result": "2",
                    }
                ],
                "steps": 1,
                "model": "gpt-4o-mini",
                "status": "completed",
                "plan": _SAMPLE_PLAN,
                "plan_steps_completed": 1,
            }
            finalize_agent_run_result(
                result,
                tenant_id="admin",
                model="gpt-4o-mini",
                plan=_SAMPLE_PLAN,
            )
            return result

        mock_graph.side_effect = fake_graph

        settings = __import__(
            "apps.gateway.settings", fromlist=["get_settings"]
        ).get_settings()
        with patch.object(settings, "llm_api_key", "sk-test"):
            run_resp = self.client.post(
                "/v1/agent/run",
                headers=_ADMIN_HEADERS,
                json={
                    "tenant_id": "admin",
                    "session_id": "run-e2e-session",
                    "auto_plan": True,
                    "goal": _GOAL,
                },
            )
        self.assertEqual(run_resp.status_code, 200, run_resp.text)

        async def drain_tasks() -> None:
            await asyncio.sleep(0.05)

        asyncio.run(drain_tasks())

        sig = compute_task_signature(_GOAL)
        store = get_experience_store()
        records = asyncio.run(store.retrieve_similar(sig, task_embedding=None, top_k=3))
        self.assertTrue(records, "run 后应写入 experience store")
        self.assertIn(_LESSON_MARKER, records[0].lessons)

        captured: dict[str, str] = {}

        async def fake_route(payload, requested_model=None, tenant_default=None):
            messages = payload.get("messages") or []
            if len(messages) > 1:
                captured["user_prompt"] = messages[1].get("content", "")

            class R:
                status = 200
                body = {"choices": [{"message": {"content": _PLAN_JSON}}]}
                error = None

            return R()

        with (
            patch.object(settings, "llm_api_key", "sk-test"),
            patch("packages.agent.planner.is_structured_mode", return_value=False),
            patch("packages.agent.planner.forward_with_model_router", side_effect=fake_route),
        ):
            plan_resp = self.client.post(
                "/v1/agent/plan",
                headers=_ADMIN_HEADERS,
                json={"tenant_id": "admin", "goal": _GOAL},
            )

        self.assertEqual(plan_resp.status_code, 200, plan_resp.text)
        user_prompt = captured.get("user_prompt", "")
        self.assertIn("历史经验", user_prompt)
        self.assertIn(_LESSON_MARKER, user_prompt)


if __name__ == "__main__":
    unittest.main()
