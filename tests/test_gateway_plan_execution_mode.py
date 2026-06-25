"""Gateway PLAN_EXECUTION_MODE — parallel / serial 切换。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from packages.agent.planner import (
    execute_plan_parallel,
    execute_plan_with_agent,
    get_plan_executor,
    is_parallel_plan_execution,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep

SAMPLE_PLAN = AgentPlan(
    goal="test goal",
    steps=[PlanStep(id="s1", description="step one", depends_on=[])],
)

ADMIN_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}


class TestPlanExecutionModeHelper(unittest.TestCase):
    def test_parallel_mode_explicit(self) -> None:
        self.assertTrue(is_parallel_plan_execution("parallel"))

    def test_settings_default_parallel(self) -> None:
        with patch("packages.agent.planner.get_settings") as mock_settings:
            mock_settings.return_value.plan_execution_mode = "parallel"
            self.assertTrue(is_parallel_plan_execution(None))

    def test_serial_mode(self) -> None:
        self.assertFalse(is_parallel_plan_execution("serial"))
        self.assertFalse(is_parallel_plan_execution("SERIAL"))

    def test_get_plan_executor_parallel(self) -> None:
        self.assertIs(get_plan_executor(mode="parallel"), execute_plan_parallel)

    def test_get_plan_executor_serial(self) -> None:
        self.assertIs(get_plan_executor(mode="serial"), execute_plan_with_agent)


class TestGatewayPlanExecutionMode(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(__import__("apps.gateway.main", fromlist=["create_app"]).create_app())

    def _post_auto_plan(self) -> int:
        return self.client.post(
            "/v1/agent/run",
            headers=ADMIN_HEADERS,
            json={
                "tenant_id": "admin",
                "session_id": "plan-exec-mode-test",
                "auto_plan": True,
                "goal": "calc 1+1",
                "model": "chat-fast",
            },
        ).status_code

    @patch("apps.gateway.agent.routes.get_plan_executor")
    @patch("apps.gateway.agent.routes.generate_plan", new_callable=AsyncMock)
    def test_gateway_uses_get_plan_executor(
        self,
        mock_generate: AsyncMock,
        mock_get_executor: MagicMock,
    ) -> None:
        mock_generate.return_value = (SAMPLE_PLAN, "chat-fast")
        mock_execute = AsyncMock(
            return_value={
                "tenant_id": "admin",
                "session_id": "plan-exec-mode-test",
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "chat-fast",
                "status": "completed",
                "plan": SAMPLE_PLAN,
                "plan_steps_completed": 1,
            }
        )
        mock_get_executor.return_value = mock_execute

        settings = __import__("apps.gateway.settings", fromlist=["get_settings"]).get_settings()
        with patch.object(settings, "llm_api_key", "sk-test"):
            with patch.object(settings, "plan_execution_mode", "parallel"):
                status = self._post_auto_plan()

        self.assertEqual(status, 200)
        mock_get_executor.assert_called_once_with(mode="parallel")
        mock_execute.assert_awaited_once()

    @patch("apps.gateway.agent.routes.get_plan_executor")
    @patch("apps.gateway.agent.routes.generate_plan", new_callable=AsyncMock)
    def test_gateway_serial_mode_passes_setting(
        self,
        mock_generate: AsyncMock,
        mock_get_executor: MagicMock,
    ) -> None:
        mock_generate.return_value = (SAMPLE_PLAN, "chat-fast")
        mock_execute = AsyncMock(
            return_value={
                "tenant_id": "admin",
                "session_id": "plan-exec-mode-test",
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "chat-fast",
                "status": "completed",
                "plan": SAMPLE_PLAN,
                "plan_steps_completed": 1,
            }
        )
        mock_get_executor.return_value = mock_execute

        settings = __import__("apps.gateway.settings", fromlist=["get_settings"]).get_settings()
        with patch.object(settings, "llm_api_key", "sk-test"):
            with patch.object(settings, "plan_execution_mode", "serial"):
                status = self._post_auto_plan()

        self.assertEqual(status, 200)
        mock_get_executor.assert_called_once_with(mode="serial")
        mock_execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
