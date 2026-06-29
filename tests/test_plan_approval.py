#!/usr/bin/env python3
"""tests/test_plan_approval.py — Phase Q Q4 Plan-level HITL tests."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.plan_approval import (  # noqa: E402
    approve_plan,
    get_plan_approval,
    is_plan_approved,
    new_plan_approval_id,
    reject_plan,
    reset_plan_approval_store_for_tests,
    store_plan_approval,
)
from packages.agent.planner import (
    execute_plan_parallel,
    execute_plan_with_agent,
    format_plan_summary,
)  # noqa: E402
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(sid: str, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(id=sid, description=f"step {sid}", depends_on=depends_on or [])


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


def _make_runner(status: str = "completed") -> object:
    async def fake_run(**kwargs):
        return {
            "final_message": "done",
            "tool_calls": [],
            "steps": 1,
            "model": "m",
            "status": status,
        }

    return fake_run


# ---------------------------------------------------------------------------
# PlanApprovalStore unit tests
# ---------------------------------------------------------------------------


class TestPlanApprovalStore(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    def test_store_creates_entry(self) -> None:
        """store() should create a retrievable entry."""
        plan = _plan(_step("s1"))
        aid = new_plan_approval_id()
        store_plan_approval(aid, plan, tenant_id="t1")
        entry = get_plan_approval(aid)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.tenant_id, "t1")
        self.assertEqual(entry.status, "pending")

    def test_get_nonexistent_returns_none(self) -> None:
        """Querying a non-existent ID returns None."""
        self.assertIsNone(get_plan_approval("nonexistent-id"))

    def test_approve_success(self) -> None:
        """approve_plan() should set status to approved."""
        plan = _plan(_step("s1"))
        aid = new_plan_approval_id()
        store_plan_approval(aid, plan, tenant_id="t1")
        result = approve_plan(aid)
        self.assertTrue(result)
        entry = get_plan_approval(aid)
        assert entry is not None
        self.assertEqual(entry.status, "approved")
        self.assertTrue(is_plan_approved(aid))

    def test_approve_nonexistent_returns_false(self) -> None:
        """approve_plan() on non-existent ID returns False."""
        self.assertFalse(approve_plan("bad-id"))

    def test_reject_success(self) -> None:
        """reject_plan() should set status to rejected."""
        plan = _plan(_step("s1"))
        aid = new_plan_approval_id()
        store_plan_approval(aid, plan, tenant_id="t1")
        result = reject_plan(aid)
        self.assertTrue(result)
        entry = get_plan_approval(aid)
        assert entry is not None
        self.assertEqual(entry.status, "rejected")
        self.assertFalse(is_plan_approved(aid))

    def test_reject_nonexistent_returns_false(self) -> None:
        """reject_plan() on non-existent ID returns False."""
        self.assertFalse(reject_plan("bad-id"))

    def test_new_plan_approval_id_unique(self) -> None:
        """new_plan_approval_id() should generate unique IDs."""
        ids = {new_plan_approval_id() for _ in range(10)}
        self.assertEqual(len(ids), 10)

    def test_store_multiple_entries(self) -> None:
        """Multiple entries can be stored independently."""
        plan = _plan(_step("s1"))
        aid1 = new_plan_approval_id()
        aid2 = new_plan_approval_id()
        store_plan_approval(aid1, plan, tenant_id="t1")
        store_plan_approval(aid2, plan, tenant_id="t2")
        approve_plan(aid1)
        self.assertTrue(is_plan_approved(aid1))
        self.assertFalse(is_plan_approved(aid2))

    def test_to_dict_serialization(self) -> None:
        """PlanApprovalRequest.to_dict() should include required keys."""
        plan = _plan(_step("s1"), _step("s2"))
        aid = new_plan_approval_id()
        req = store_plan_approval(aid, plan, tenant_id="t1", session_id="sess1")
        d = req.to_dict()
        self.assertIn("plan_approval_id", d)
        self.assertIn("tenant_id", d)
        self.assertIn("status", d)
        self.assertIn("plan", d)
        self.assertEqual(d["plan"]["goal"], "test goal")
        self.assertEqual(len(d["plan"]["steps"]), 2)


# ---------------------------------------------------------------------------
# format_plan_summary tests
# ---------------------------------------------------------------------------


class TestFormatPlanSummary(unittest.TestCase):
    def test_summary_contains_goal(self) -> None:
        """Summary should include the plan goal."""
        plan = _plan(_step("s1"))
        plan.goal = "我要分析数据"
        summary = format_plan_summary(plan)
        self.assertIn("我要分析数据", summary)

    def test_summary_contains_step_descriptions(self) -> None:
        """Summary should include all step descriptions."""
        s1 = PlanStep(id="s1", description="收集数据", depends_on=[])
        s2 = PlanStep(id="s2", description="清洗数据", depends_on=["s1"])
        plan = AgentPlan(goal="分析", steps=[s1, s2])
        summary = format_plan_summary(plan)
        self.assertIn("收集数据", summary)
        self.assertIn("清洗数据", summary)

    def test_summary_contains_step_ids(self) -> None:
        """Summary should include step IDs."""
        plan = _plan(_step("s1"), _step("s2"))
        summary = format_plan_summary(plan)
        self.assertIn("s1", summary)
        self.assertIn("s2", summary)


# ---------------------------------------------------------------------------
# execute_plan_with_agent + require_plan_approval tests
# ---------------------------------------------------------------------------


class TestExecutePlanWithAgentApproval(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    def test_pending_plan_approval_when_required(self) -> None:
        """With require_plan_approval=True, should return pending_plan_approval without executing."""
        s1 = _step("s1")
        plan = _plan(s1)
        call_count = {"n": 0}

        async def fake_run(**kwargs):
            call_count["n"] += 1
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = asyncio.run(
            execute_plan_with_agent(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
                require_plan_approval=True,
            )
        )
        # No steps executed
        self.assertEqual(call_count["n"], 0)
        self.assertEqual(result["status"], "pending_plan_approval")
        self.assertIn("plan_approval_id", result)
        self.assertIsNotNone(result["plan_approval_id"])
        self.assertEqual(result["plan_steps_completed"], 0)

    def test_no_approval_by_default(self) -> None:
        """Default require_plan_approval=False should execute normally."""
        s1 = _step("s1")
        plan = _plan(s1)

        result = asyncio.run(
            execute_plan_with_agent(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=_make_runner("completed"),
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan_steps_completed"], 1)

    def test_plan_stored_in_approval_store(self) -> None:
        """When pending_plan_approval, plan should be stored in plan_approval store."""
        plan = _plan(_step("s1"))

        result = asyncio.run(
            execute_plan_with_agent(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=_make_runner(),
                require_plan_approval=True,
            )
        )
        aid = result["plan_approval_id"]
        entry = get_plan_approval(aid)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.status, "pending")
        self.assertEqual(entry.tenant_id, "t1")


# ---------------------------------------------------------------------------
# execute_plan_parallel + require_plan_approval tests
# ---------------------------------------------------------------------------


class TestExecutePlanParallelApproval(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    def test_parallel_pending_plan_approval(self) -> None:
        """execute_plan_parallel also supports require_plan_approval."""
        plan = _plan(_step("s1"), _step("s2"))
        call_count = {"n": 0}

        async def fake_run(**kwargs):
            call_count["n"] += 1
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = asyncio.run(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
                require_plan_approval=True,
            )
        )
        self.assertEqual(call_count["n"], 0)
        self.assertEqual(result["status"], "pending_plan_approval")
        self.assertIn("plan_approval_id", result)


if __name__ == "__main__":
    unittest.main()
