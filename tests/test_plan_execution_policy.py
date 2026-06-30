#!/usr/bin/env python3
"""tests/test_plan_execution_policy.py — #174 PR-6b plan HITL/replan policy。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.plan_approval import (  # noqa: E402
    get_plan_approval,
    reset_plan_approval_store_for_tests,
)
from packages.agent.plan_execution_policy import (  # noqa: E402
    append_replan_revision,
    gate_plan_approval_or_none,
    plan_execution_result,
    should_gate_plan_approval,
    try_replan_and_reexecute,
)
from packages.agent.planner import format_plan_summary  # noqa: E402
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="g", steps=list(steps))


class TestPlanApprovalGate(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    def test_should_gate_only_first_attempt(self) -> None:
        self.assertTrue(
            should_gate_plan_approval(require_plan_approval=True, replan_attempt=0)
        )
        self.assertFalse(
            should_gate_plan_approval(require_plan_approval=True, replan_attempt=1)
        )
        self.assertFalse(
            should_gate_plan_approval(require_plan_approval=False, replan_attempt=0)
        )

    def test_gate_plan_approval_stores_and_returns_pending(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))
        payload = gate_plan_approval_or_none(
            plan=plan,
            tenant_id="t1",
            session_id="sess",
            model="m1",
            format_plan_summary=format_plan_summary,
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "pending_plan_approval")
        self.assertIn("plan_approval_id", payload)
        entry = get_plan_approval(payload["plan_approval_id"])
        self.assertIsNotNone(entry)


class TestPlanExecutionResult(unittest.TestCase):
    def test_payload_shape(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))
        payload = plan_execution_result(
            tenant_id="t1",
            session_id="s1",
            final_message="ok",
            tool_calls=[],
            steps=2,
            resolved_model="m",
            status="completed",
            approval_id=None,
            plan=plan,
            plan_steps_completed=1,
            plan_revisions=[],
        )
        self.assertEqual(payload["plan_steps_completed"], 1)
        self.assertIn("trace_id", payload)


class TestReplanPolicy(unittest.TestCase):
    def test_append_replan_revision(self) -> None:
        revisions: list = []
        plan = _plan(PlanStep(id="s2", description="d"))
        append_replan_revision(
            revisions,
            replan_attempt=0,
            failed_step_id="s1",
            new_plan=plan,
        )
        self.assertEqual(revisions[0]["failed_step_id"], "s1")
        self.assertEqual(revisions[0]["new_plan_steps_count"], 1)

    def test_try_replan_returns_none_when_exhausted(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))
        step = plan.steps[0]

        async def _run_try():
            return await try_replan_and_reexecute(
                plan=plan,
                failed_step=step,
                failure_reason="boom",
                model=None,
                allowed_models=("m",),
                max_replan_attempts=1,
                replan_attempt=1,
                plan_revisions=[],
                reexecute=AsyncMock(),
                log_context="test",
            )

        self.assertIsNone(_run(_run_try()))

    def test_try_replan_reexecutes_on_success(self) -> None:
        plan = _plan(PlanStep(id="s1", description="d"))
        step = plan.steps[0]
        new_plan = _plan(PlanStep(id="s1b", description="d2"))
        reexecute = AsyncMock(return_value={"status": "completed"})

        async def _run_try():
            with patch(
                "packages.agent.plan_critic.replan_after_failure",
                new=AsyncMock(return_value=new_plan),
            ):
                return await try_replan_and_reexecute(
                    plan=plan,
                    failed_step=step,
                    failure_reason="boom",
                    model=None,
                    allowed_models=("m",),
                    max_replan_attempts=2,
                    replan_attempt=0,
                    plan_revisions=[],
                    reexecute=reexecute,
                    log_context="test",
                )

        result = _run(_run_try())
        self.assertEqual(result, {"status": "completed"})
        reexecute.assert_awaited_once_with(new_plan)


if __name__ == "__main__":
    unittest.main()
