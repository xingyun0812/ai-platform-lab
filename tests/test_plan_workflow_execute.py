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
        # 依赖层 [[s1],[s2,s3],[s4]]：s2/s3 组装为 parallel 分支，s1/s4 保持顶层 plan_step
        top_step_ids = [n.node_id for n in wf.nodes if n.node_type == "plan_step"]
        self.assertEqual(top_step_ids, ["s1", "s4"])
        parallel_ids = [n.node_id for n in wf.nodes if n.node_type == "parallel"]
        self.assertEqual(parallel_ids, ["parallel_2"])
        branches = [
            b["id"] for n in wf.nodes if n.node_type == "parallel" for b in n.config["branches"]
        ]
        self.assertEqual(sorted(branches), ["s2", "s3"])
        # 顶层线性链：start → s1 → parallel_2 → s4 → end
        pairs = {(e.from_node, e.to_node) for e in wf.edges}
        self.assertIn(("start", "s1"), pairs)
        self.assertIn(("s1", "parallel_2"), pairs)
        self.assertIn(("parallel_2", "s4"), pairs)
        self.assertIn(("s4", "end"), pairs)


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

    def test_diamond_parallel_executes_in_parallel_preserves_order(self) -> None:
        """#249: 无依赖 step 并行执行，强依赖 step 串行有序，依赖不被破坏。

        记录每个 step 的 start/end 事件；验证 s2/s3 并行（重叠），且 s1 先于
        s2/s3 结束、s4 在 s2/s3 之后开始。同时断言 parallel 分支内 plan_step
        产物回写 outputs[step.id]（与 execution_engine 映射兼容）。
        """
        plan = _plan(
            "Diamond parallel",
            _step("s1", description="Fetch"),
            _step("s2", description="Branch A", depends_on=["s1"]),
            _step("s3", description="Branch B", depends_on=["s1"]),
            _step("s4", description="Merge", depends_on=["s2", "s3"]),
        )
        wf = plan_to_orchestrator_workflow(plan, workflow_id="exec-diamond")

        events: list[tuple[str, str]] = []

        def _step_id(session_id: str) -> str:
            return str(session_id).rsplit("__step_", 1)[-1]

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            step_id = _step_id(str(kwargs.get("session_id")))
            events.append((step_id, "start"))
            # 让 s2/s3 同时休眠，确保真正并发而非顺序执行
            await asyncio.sleep(0.05)
            events.append((step_id, "end"))
            return {
                "final_message": f"done-{step_id}",
                "tool_calls": [],
                "steps": 1,
                "model": "mock-model",
                "status": "completed",
            }

        inputs = {
            "tenant_id": "t1",
            "session_id": "sess-diamond",
            "allowed_tools": ("calc",),
            "allowed_models": ("mock-model",),
            "model": "mock-model",
            "session_store": None,
        }

        with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
            result = asyncio.run(execute_workflow(wf, inputs=inputs))

        self.assertEqual(result.status, "completed")
        # 四个 step 全部执行并被回写成 outputs[step.id]（parallel 分支内也成立）
        self.assertEqual(result.outputs["s1"]["status"], "completed")
        self.assertEqual(result.outputs["s2"]["status"], "completed")
        self.assertEqual(result.outputs["s3"]["status"], "completed")
        self.assertEqual(result.outputs["s4"]["status"], "completed")

        # 每个 step 恰好 start+end 一次
        self.assertEqual({sid for sid, _ in events}, {"s1", "s2", "s3", "s4"})
        # 位置以原始 events 顺序衡量（跨 start/end 可比）
        pos = {sid: {} for sid in ("s1", "s2", "s3", "s4")}
        for i, (sid, kind) in enumerate(events):
            pos[sid][kind] = i
        # 强依赖：s1 结束先于 s2/s3 开始；s4 开始晚于 s2/s3 结束
        self.assertLess(pos["s1"]["end"], pos["s2"]["start"])
        self.assertLess(pos["s1"]["end"], pos["s3"]["start"])
        self.assertGreater(pos["s4"]["start"], pos["s2"]["end"])
        self.assertGreater(pos["s4"]["start"], pos["s3"]["end"])
        # 并行证据：s2/s3 重叠执行 —— 两者 start 均早于任一端（真正并发而非串行）
        earliest_end = min(pos["s2"]["end"], pos["s3"]["end"])
        self.assertLess(pos["s2"]["start"], earliest_end)
        self.assertLess(pos["s3"]["start"], earliest_end)
        # 顶层 trace 拓扑：s1 的完成先于 parallel_2，parallel_2 先于 s4
        top_trace = result.trace
        self.assertLess(
            next(i for i, t in enumerate(top_trace) if t["node_id"] == "s1"),
            next(i for i, t in enumerate(top_trace) if t["node_id"].startswith("parallel_")),
        )
        self.assertLess(
            next(i for i, t in enumerate(top_trace) if t["node_id"].startswith("parallel_")),
            next(i for i, t in enumerate(top_trace) if t["node_id"] == "s4"),
        )


if __name__ == "__main__":
    unittest.main()
