#!/usr/bin/env python3
"""tests/test_plan_executor.py — #176 PR-6c PlanExecutionContext / PlanExecutor。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.plan_approval import reset_plan_approval_store_for_tests  # noqa: E402
from packages.agent.plan_executor import (  # noqa: E402
    PlanExecutionContext,
    execute_plan_parallel,
    execute_plan_serial,
    run_plan_execution,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="g", steps=list(steps))


class TestPlanExecutionContext(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    def test_create_returns_gate_when_approval_required(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))

        async def _go():
            return await PlanExecutionContext.create(
                plan=plan,
                tenant_id="t1",
                session_id="s1",
                allowed_tools=(),
                allowed_models=("m",),
                model="m",
                session_store=None,
                step_system_messages=None,
                runner=AsyncMock(),
                max_replan_attempts=2,
                replan_attempt=0,
                plan_revisions=[],
                require_plan_approval=True,
            )

        ctx, early = _run(_go())
        self.assertIsNone(ctx)
        self.assertIsNotNone(early)
        assert early is not None
        self.assertEqual(early["status"], "pending_plan_approval")

    def test_absorb_step_result_updates_accumulators(self) -> None:
        ctx = PlanExecutionContext(
            plan=_plan(PlanStep(id="s1", description="d")),
            tenant_id="t1",
            session_id="s1",
            allowed_tools=(),
            allowed_models=("m",),
            model="m",
            session_store=None,
            step_system_messages=None,
            runner=AsyncMock(),
            max_replan_attempts=2,
            replan_attempt=0,
            plan_revisions=[],
            require_plan_approval=False,
            resolved_model="m",
        )
        status = ctx.absorb_step_result(
            {
                "final_message": "ok",
                "steps": 2,
                "model": "m2",
                "status": "completed",
                "tool_calls": [],
            }
        )
        self.assertEqual(status, "completed")
        self.assertEqual(ctx.agent_steps, 2)
        self.assertEqual(ctx.resolved_model, "m2")


class TestPlanExecutor(unittest.TestCase):
    def test_run_plan_execution_serial(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))
        runner = AsyncMock(
            return_value={
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }
        )

        async def _go():
            return await run_plan_execution(
                parallel=False,
                plan=plan,
                tenant_id="t1",
                session_id="s1",
                allowed_tools=(),
                allowed_models=("m",),
                model="m",
                session_store=None,
                step_system_messages=None,
                runner=runner,
                max_replan_attempts=2,
                replan_attempt=0,
                plan_revisions=[],
                require_plan_approval=False,
            )

        result = _run(_go())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan_steps_completed"], 1)
        runner.assert_awaited_once()

    def test_execute_plan_serial_delegates_steps_in_order(self) -> None:
        plan = _plan(
            PlanStep(id="s1", description="a"),
            PlanStep(id="s2", description="b", depends_on=["s1"]),
        )
        order: list[str] = []

        async def runner(**kwargs):
            content = kwargs["new_messages"][0]["content"]
            if "· s1]" in content:
                order.append("s1")
            elif "· s2]" in content:
                order.append("s2")
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        ctx = PlanExecutionContext(
            plan=plan,
            tenant_id="t1",
            session_id="s1",
            allowed_tools=(),
            allowed_models=("m",),
            model="m",
            session_store=None,
            step_system_messages=None,
            runner=runner,
            max_replan_attempts=2,
            replan_attempt=0,
            plan_revisions=[],
            require_plan_approval=False,
            resolved_model="m",
        )
        result = _run(execute_plan_serial(ctx))
        self.assertEqual(order, ["s1", "s2"])
        self.assertEqual(result["plan_steps_completed"], 2)

    def test_execute_plan_parallel_uses_sub_sessions(self) -> None:
        plan = _plan(
            PlanStep(id="s1", description="a"),
            PlanStep(id="s2", description="b"),
        )
        sessions: list[str] = []

        async def runner(**kwargs):
            sessions.append(kwargs["session_id"])
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        ctx = PlanExecutionContext(
            plan=plan,
            tenant_id="t1",
            session_id="root",
            allowed_tools=(),
            allowed_models=("m",),
            model="m",
            session_store=None,
            step_system_messages=None,
            runner=runner,
            max_replan_attempts=2,
            replan_attempt=0,
            plan_revisions=[],
            require_plan_approval=False,
            resolved_model="m",
        )
        with patch("packages.agent.plan_executor.get_agent_perf_metrics") as mock_metrics:
            mock_metrics.return_value.record_parallel_steps = MagicMock()
            result = _run(execute_plan_parallel(ctx))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(s.startswith("root__step_") for s in sessions))


if __name__ == "__main__":
    unittest.main()
