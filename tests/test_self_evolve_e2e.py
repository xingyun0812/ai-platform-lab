"""tests/test_self_evolve_e2e.py — #146 gateway E2E：REST approve → plan 注入。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.gateway_client import LifespanTestClient
from packages.agent.experience_store import reset_experience_store_for_tests
from packages.agent.self_evolve import StrategyPatch, reset_strategy_patch_store_for_tests

_E2E_MARKER = "E2E_APPROVED_STRATEGY_MARKER"
_PLAN_JSON = (
    '{"goal":"分析销售","steps":[{"id":"s1","description":"汇总","depends_on":[]}]}'
)


def _admin_headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }


class TestSelfEvolveGatewayE2E(unittest.TestCase):
    """REST approve strategy patch → POST /v1/agent/plan 应注入【已审批策略】。"""

    def setUp(self) -> None:
        reset_strategy_patch_store_for_tests()
        reset_experience_store_for_tests()
        self._client_cm = LifespanTestClient()
        self.client = self._client_cm.__enter__()
        store = __import__(
            "packages.agent.self_evolve",
            fromlist=["get_strategy_patch_store"],
        ).get_strategy_patch_store()
        store.add(
            StrategyPatch(
                patch_id="patch-e2e-1",
                tenant_id="admin",
                lessons="lessons from run",
                proposed_change={
                    "field": "plan_prompt",
                    "old": "default",
                    "new": _E2E_MARKER,
                    "reason": "e2e test",
                },
                status="pending",
            )
        )

    def tearDown(self) -> None:
        reset_strategy_patch_store_for_tests()
        reset_experience_store_for_tests()

    @patch("packages.agent.planner.is_structured_mode", return_value=False)
    @patch("packages.agent.planner.forward_with_model_router")
    def test_rest_approve_then_plan_injects_strategy(
        self,
        mock_route,
        _mock_structured,
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

        approve = self.client.post(
            "/internal/agent/strategy-patches/patch-e2e-1/approve",
            headers=_admin_headers(),
        )
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.json()["status"], "approved")

        plan_resp = self.client.post(
            "/v1/agent/plan",
            json={"tenant_id": "admin", "goal": "分析销售数据"},
            headers=_admin_headers(),
        )
        self.assertEqual(plan_resp.status_code, 200)
        self.assertEqual(plan_resp.json()["goal"], "分析销售")

        user_prompt = captured.get("user_prompt", "")
        self.assertIn(_E2E_MARKER, user_prompt)
        self.assertIn("已审批策略", user_prompt)

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
