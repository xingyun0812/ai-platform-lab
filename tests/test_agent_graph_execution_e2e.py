#!/usr/bin/env python3
"""tests/test_agent_graph_execution_e2e.py — graph_runtime → ExecutionEngine E2E (#162 PR-4)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.graph_runtime import execute_agent_graph  # noqa: E402
from packages.contracts.agent_schemas import AgentPlan, AgentRunRequest, PlanStep  # noqa: E402
from packages.platform import configure, reset_platform_for_tests  # noqa: E402
from packages.platform.testing import InMemoryPlatformPort, InMemoryPlatformSettings  # noqa: E402

SAMPLE_PLAN = AgentPlan(
    goal="E2E orchestrator goal",
    steps=[
        PlanStep(id="s1", description="step one"),
        PlanStep(id="s2", description="step two", depends_on=["s1"]),
    ],
)


class TestAgentGraphExecutionE2E(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()
        settings = InMemoryPlatformSettings(
            plan_execution_backend="orchestrator",
            plan_require_approval=False,
            plan_max_replan_attempts=2,
        )
        configure(InMemoryPlatformPort(settings=settings))

    def tearDown(self) -> None:
        reset_platform_for_tests()

    async def test_auto_plan_orchestrator_backend_e2e(self) -> None:
        tenant = MagicMock(
            tenant_id="admin",
            allowed_tools=(),
            allowed_models=("test-model",),
        )
        body = AgentRunRequest(
            tenant_id="admin",
            session_id="e2e-session",
            auto_plan=True,
            goal="run two steps",
            model="test-model",
        )

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            return {
                "final_message": f"done-{kwargs.get('session_id')}",
                "tool_calls": [],
                "steps": 1,
                "model": "test-model",
                "status": "completed",
            }

        with patch(
            "packages.agent.graph_runtime.generate_plan",
            new=AsyncMock(return_value=(SAMPLE_PLAN, "test-model")),
        ):
            with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
                with patch(
                    "packages.agent.graph_runtime.finalize_agent_run_result",
                    side_effect=lambda r, **kw: r,
                ):
                    result = await execute_agent_graph(
                        body=body,
                        tenant=tenant,
                        session_store=MagicMock(),
                        new_messages=[],
                        step_system_messages=None,
                        shadow_mode=False,
                    )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["execution_backend"], "orchestrator")
        self.assertEqual(result["plan_steps_completed"], 2)
        self.assertIn("workflow_trace", result)
        graph_state = result.get("_graph_state") or {}
        self.assertEqual(graph_state.get("mode"), "plan")
        self.assertEqual(graph_state.get("status"), "completed")

    async def test_plan_approval_resume_uses_execution_engine(self) -> None:
        from packages.agent.plan_approval import (
            approve_plan,
            reset_plan_approval_store_for_tests,
            store_plan_approval,
        )

        reset_plan_approval_store_for_tests()
        aid = "e2e-plan-approval"
        store_plan_approval(aid, SAMPLE_PLAN, tenant_id="admin", session_id="e2e-session")
        approve_plan(aid)

        tenant = MagicMock(
            tenant_id="admin",
            allowed_tools=(),
            allowed_models=("test-model",),
        )
        body = AgentRunRequest(
            tenant_id="admin",
            session_id="e2e-session",
            plan_approval_id=aid,
            model="test-model",
        )

        with patch(
            "packages.agent.graph_runtime.run_plan_execution",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "execution_backend": "planner",
                    "plan": SAMPLE_PLAN,
                    "final_message": "resumed",
                    "tool_calls": [],
                }
            ),
        ) as run_plan:
            with patch(
                "packages.agent.graph_runtime.finalize_agent_run_result",
                side_effect=lambda r, **kw: r,
            ):
                result = await execute_agent_graph(
                    body=body,
                    tenant=tenant,
                    session_store=MagicMock(),
                    new_messages=[],
                    step_system_messages=None,
                    shadow_mode=False,
                )

        run_plan.assert_awaited_once()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resumed_from_plan_approval_id"], aid)


if __name__ == "__main__":
    unittest.main()
