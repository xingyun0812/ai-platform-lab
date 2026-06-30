#!/usr/bin/env python3
"""tests/test_plan_workflow_execute.py — Plan → Orchestrator Workflow 执行桥接 (#162 PR-1)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.orchestrator.engine import execute_workflow  # noqa: E402
from packages.agent.plan_workflow import plan_to_orchestrator_workflow  # noqa: E402
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _step(
    sid: str,
    description: str = "",
    depends_on: list[str] | None = None,
    tool_hint: str | None = None,
) -> PlanStep:
    return PlanStep(
        id=sid,
        description=description or f"step {sid}",
        depends_on=depends_on or [],
        tool_hint=tool_hint,
    )


def _plan(goal: str, *steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal=goal, steps=list(steps))


class TestPlanToOrchestratorWorkflow(unittest.TestCase):
    def test_has_start_end_and_plan_step_nodes(self) -> None:
        plan = _plan("Analyze", _step("s1"), _step("s2", depends_on=["s1"]))
        wf = plan_to_orchestrator_workflow(plan, workflow_id="wf-test")
        types = {n.node_id: n.node_type for n in wf.nodes}
        self.assertEqual(types["start"], "start")
        self.assertEqual(types["end"], "end")
        self.assertEqual(types["s1"], "plan_step")
        self.assertEqual(types["s2"], "plan_step")

    def test_linear_chain_edges(self) -> None:
        plan = _plan("Goal", _step("s1"), _step("s2", depends_on=["s1"]))
        wf = plan_to_orchestrator_workflow(plan)
        pairs = {(e.from_node, e.to_node) for e in wf.edges}
        self.assertIn(("start", "s1"), pairs)
        self.assertIn(("s1", "s2"), pairs)
        self.assertIn(("s2", "end"), pairs)

    def test_cycle_raises(self) -> None:
        plan = _plan(
            "Cycle",
            _step("s1", depends_on=["s2"]),
            _step("s2", depends_on=["s1"]),
        )
        with self.assertRaises(ValueError):
            plan_to_orchestrator_workflow(plan)

    def test_diamond_topological_order(self) -> None:
        plan = _plan(
            "Diamond",
            _step("s1"),
            _step("s2", depends_on=["s1"]),
            _step("s3", depends_on=["s1"]),
            _step("s4", depends_on=["s2", "s3"]),
        )
        wf = plan_to_orchestrator_workflow(plan)
        step_ids = [n.node_id for n in wf.nodes if n.node_type == "plan_step"]
        self.assertEqual(step_ids, ["s1", "s2", "s3", "s4"])


class TestPlanWorkflowExecute(unittest.TestCase):
    def test_linear_two_step_execute(self) -> None:
        plan = _plan(
            "Two step goal",
            _step("s1", description="Fetch data"),
            _step("s2", description="Summarize", depends_on=["s1"]),
        )
        wf = plan_to_orchestrator_workflow(plan, workflow_id="exec-linear")

        call_count = 0

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            session_id = kwargs.get("session_id")
            return {
                "final_message": f"done-{session_id}",
                "tool_calls": [],
                "steps": 1,
                "model": "mock-model",
                "status": "completed",
            }

        inputs = {
            "tenant_id": "t1",
            "session_id": "sess-1",
            "allowed_tools": ("calc",),
            "allowed_models": ("mock-model",),
            "model": "mock-model",
            "session_store": None,
        }

        with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
            result = asyncio.run(execute_workflow(wf, inputs=inputs))

        self.assertEqual(result.status, "completed")
        self.assertEqual(call_count, 2)
        completed_steps = [
            t["node_id"]
            for t in result.trace
            if t.get("status") == "completed" and t["node_id"] in {"s1", "s2"}
        ]
        self.assertEqual(completed_steps, ["s1", "s2"])
        self.assertIn("s1", result.outputs)
        self.assertIn("s2", result.outputs)

    def test_plan_step_uses_tool_hint(self) -> None:
        plan = _plan("One step", _step("s1", description="Calc", tool_hint="calc"))
        wf = plan_to_orchestrator_workflow(plan)
        captured: dict[str, object] = {}

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
            result = asyncio.run(
                execute_workflow(
                    wf,
                    inputs={
                        "tenant_id": "t1",
                        "session_id": "sess",
                        "allowed_tools": ("calc",),
                        "allowed_models": ("m",),
                    },
                )
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(captured.get("pinned_tools"), ("calc",))


if __name__ == "__main__":
    unittest.main()
