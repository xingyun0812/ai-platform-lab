"""tests/test_experience_plan_e2e.py — Phase R 经验注入 gateway E2E (#183)。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent.experience_store import (
    build_experience_record,
    reset_experience_store_for_tests,
    store_experience,
)
from packages.agent.self_evolve import reset_strategy_patch_store_for_tests
from packages.contracts.agent_schemas import AgentPlan
from tests.gateway_client import LifespanTestClient

_EXPERIENCE_MARKER = "E2E_EXPERIENCE_LESSON_MARKER"
_GOAL = "分析 Q2 销售数据趋势"
_PLAN_JSON = (
    '{"goal":"分析 Q2 销售数据趋势","steps":[{"id":"s1","description":"汇总","depends_on":[]}]}'
)


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }


class TestExperiencePlanGatewayE2E(unittest.TestCase):
    """store experience → POST /v1/agent/plan 应注入【历史经验】。"""

    def setUp(self) -> None:
        reset_experience_store_for_tests()
        reset_strategy_patch_store_for_tests()
        plan = AgentPlan(
            goal=_GOAL,
            steps=[{"id": "s1", "description": "汇总", "depends_on": []}],
        )
        record = build_experience_record(
            tenant_id="admin",
            goal=_GOAL,
            plan=plan,
            outcome="success",
            lessons=_EXPERIENCE_MARKER,
        )
        asyncio.run(store_experience(record))
        self._client_cm = LifespanTestClient()
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        reset_experience_store_for_tests()
        reset_strategy_patch_store_for_tests()

    @patch("packages.agent.experience_store.compute_task_embedding", new_callable=AsyncMock, return_value=None)
    @patch("packages.agent.planner.is_structured_mode", return_value=False)
    @patch("packages.agent.planner.forward_with_model_router")
    def test_plan_injects_past_experience(
        self,
        mock_route,
        _mock_structured,
        _mock_embedding,
    ) -> None:
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

        mock_route.side_effect = fake_route

        plan_resp = self.client.post(
            "/v1/agent/plan",
            json={"tenant_id": "admin", "goal": _GOAL},
            headers=_admin_headers(),
        )
        self.assertEqual(plan_resp.status_code, 200, plan_resp.text)
        user_prompt = captured.get("user_prompt", "")
        self.assertIn("历史经验", user_prompt)
        self.assertIn(_EXPERIENCE_MARKER, user_prompt)


if __name__ == "__main__":
    unittest.main()
