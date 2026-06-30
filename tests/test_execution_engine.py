#!/usr/bin/env python3
"""tests/test_execution_engine.py — ExecutionEngine facade (#162 PR-2)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.execution_engine import (  # noqa: E402
    execute_plan,
    resolve_plan_execution_backend,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


class TestResolveBackend(unittest.TestCase):
    def test_default_orchestrator(self) -> None:
        mock_settings = MagicMock(plan_execution_backend="orchestrator")
        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            self.assertEqual(resolve_plan_execution_backend(), "orchestrator")

    def test_explicit_planner(self) -> None:
        mock_settings = MagicMock(plan_execution_backend="planner")
        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            self.assertEqual(resolve_plan_execution_backend(), "planner")

    def test_unknown_falls_back_planner(self) -> None:
        mock_settings = MagicMock(plan_execution_backend="unknown")
        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            self.assertEqual(resolve_plan_execution_backend(), "planner")


class TestExecutePlanRouting(unittest.IsolatedAsyncioTestCase):
    async def test_planner_backend_delegates(self) -> None:
        plan = _plan(PlanStep(id="s1", description="do it"))
        mock_executor = AsyncMock(return_value={"status": "completed", "plan": plan})
        mock_settings = MagicMock(
            plan_execution_backend="planner",
            plan_execution_mode="serial",
            plan_max_replan_attempts=2,
        )
        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            with patch(
                "packages.agent.execution_engine.get_plan_executor",
                return_value=mock_executor,
            ) as get_exec:
                result = await execute_plan(
                    plan=plan,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=(),
                    allowed_models=(),
                    model="m",
                    session_store=None,
                )
        get_exec.assert_called_once_with(mode="serial")
        mock_executor.assert_awaited_once()
        self.assertEqual(result["status"], "completed")

    async def test_orchestrator_backend_runs_workflow(self) -> None:
        plan = _plan(
            PlanStep(id="s1", description="step one"),
            PlanStep(id="s2", description="step two", depends_on=["s1"]),
        )
        mock_settings = MagicMock(
            plan_execution_backend="orchestrator",
            plan_execution_mode="serial",
            plan_max_replan_attempts=2,
        )

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            sid = kwargs.get("session_id", "")
            return {
                "final_message": f"ok-{sid}",
                "tool_calls": [],
                "steps": 1,
                "model": "mock",
                "status": "completed",
            }

        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
                result = await execute_plan(
                    plan=plan,
                    tenant_id="t1",
                    session_id="sess",
                    allowed_tools=(),
                    allowed_models=(),
                    model="mock",
                    session_store=None,
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["execution_backend"], "orchestrator")
        self.assertEqual(result["plan_steps_completed"], 2)
        self.assertIn("workflow_trace", result)

    async def test_orchestrator_falls_back_for_plan_approval(self) -> None:
        plan = _plan(PlanStep(id="s1", description="x"))
        mock_executor = AsyncMock(return_value={"status": "pending_plan_approval"})
        mock_settings = MagicMock(
            plan_execution_backend="orchestrator",
            plan_execution_mode="parallel",
            plan_max_replan_attempts=2,
        )
        with patch("packages.agent.execution_engine.get_settings", return_value=mock_settings):
            with patch(
                "packages.agent.execution_engine.get_plan_executor",
                return_value=mock_executor,
            ) as get_exec:
                result = await execute_plan(
                    plan=plan,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=(),
                    allowed_models=(),
                    model=None,
                    session_store=None,
                    require_plan_approval=True,
                )
        get_exec.assert_called_once()
        self.assertEqual(result["status"], "pending_plan_approval")


class TestGraphRuntimeOrchestratorBackend(unittest.IsolatedAsyncioTestCase):
    async def test_auto_plan_uses_execution_engine(self) -> None:
        from packages.agent.graph_runtime import execute_agent_graph
        from packages.contracts.agent_schemas import AgentRunRequest

        plan = _plan(PlanStep(id="s1", description="calc"))
        tenant = MagicMock(
            tenant_id="admin",
            allowed_tools=(),
            allowed_models=(),
        )
        body = AgentRunRequest(
            tenant_id="admin",
            session_id="sess",
            auto_plan=True,
            goal="do calc",
        )
        mock_settings = MagicMock(
            plan_require_approval=False,
            plan_max_replan_attempts=2,
            plan_execution_backend="orchestrator",
        )

        with patch("packages.agent.graph_runtime.get_settings", return_value=mock_settings):
            with patch(
                "packages.agent.graph_runtime.generate_plan",
                new=AsyncMock(return_value=(plan, "mock-model")),
            ):
                with patch(
                    "packages.agent.graph_runtime.run_plan_execution",
                    new=AsyncMock(
                        return_value={
                            "status": "completed",
                            "execution_backend": "orchestrator",
                            "plan": plan,
                            "final_message": "done",
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
        self.assertEqual(result["execution_backend"], "orchestrator")


if __name__ == "__main__":
    unittest.main()
