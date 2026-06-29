#!/usr/bin/env python3
"""tests/test_plan_parallel.py — Phase Q #117 DAG parallel plan step execution."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.perf_metrics import (  # noqa: E402
    reset_agent_perf_metrics_for_tests,
)
from packages.agent.planner import (  # noqa: E402
    execute_plan_parallel,
    plan_execution_layers,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _step(sid: str, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(id=sid, description=f"step {sid}", depends_on=depends_on or [])


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


def _run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# plan_execution_layers tests
# ---------------------------------------------------------------------------


class TestPlanExecutionLayers(unittest.TestCase):
    def test_plan_execution_layers_empty(self) -> None:
        """Empty steps list should return empty list of layers."""
        result = plan_execution_layers([])
        self.assertEqual(result, [])

    def test_plan_execution_layers_single(self) -> None:
        """Single step with no deps → [[step]]."""
        s1 = _step("s1")
        result = plan_execution_layers([s1])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], [s1])

    def test_plan_execution_layers_no_deps(self) -> None:
        """All independent steps → 1 layer with all steps."""
        s1, s2, s3 = _step("s1"), _step("s2"), _step("s3")
        result = plan_execution_layers([s1, s2, s3])
        self.assertEqual(len(result), 1)
        self.assertCountEqual(result[0], [s1, s2, s3])

    def test_plan_execution_layers_linear(self) -> None:
        """s1 → s2 → s3 → 3 layers, each with 1 step."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])
        s3 = _step("s3", ["s2"])
        result = plan_execution_layers([s1, s2, s3])
        self.assertEqual(len(result), 3)
        self.assertEqual([s.id for layer in result for s in layer], ["s1", "s2", "s3"])

    def test_plan_execution_layers_parallel(self) -> None:
        """s1 → [s2, s3] → s4 returns 3 layers."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])
        s3 = _step("s3", ["s1"])
        s4 = _step("s4", ["s2", "s3"])
        result = plan_execution_layers([s1, s2, s3, s4])
        self.assertEqual(len(result), 3)
        self.assertEqual([s.id for s in result[0]], ["s1"])
        self.assertCountEqual([s.id for s in result[1]], ["s2", "s3"])
        self.assertEqual([s.id for s in result[2]], ["s4"])

    def test_plan_execution_layers_diamond_shape(self) -> None:
        """Diamond dependency: s1 → s2, s1 → s3, s2 → s4, s3 → s4."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])
        s3 = _step("s3", ["s1"])
        s4 = _step("s4", ["s2", "s3"])
        result = plan_execution_layers([s1, s2, s3, s4])
        ids = [[s.id for s in layer] for layer in result]
        self.assertEqual(ids[0], ["s1"])
        self.assertCountEqual(ids[1], ["s2", "s3"])
        self.assertEqual(ids[2], ["s4"])

    def test_plan_execution_layers_preserves_all_steps(self) -> None:
        """All steps must appear in the layers exactly once."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])
        s3 = _step("s3", ["s1"])
        s4 = _step("s4", ["s2"])
        steps = [s1, s2, s3, s4]
        result = plan_execution_layers(steps)
        flattened = [s for layer in result for s in layer]
        self.assertCountEqual(flattened, steps)


# ---------------------------------------------------------------------------
# execute_plan_parallel tests
# ---------------------------------------------------------------------------


class TestExecutePlanParallel(unittest.TestCase):
    def setUp(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def _make_runner(self, return_val: dict | None = None, raise_exc: Exception | None = None):
        async def fake_run(**kwargs):
            if raise_exc is not None:
                raise raise_exc
            return return_val or {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-mock",
                "status": "completed",
            }

        return fake_run

    def test_execute_plan_parallel_simple(self) -> None:
        """1 layer with 2 independent steps → plan_steps_completed=2."""
        s1, s2 = _step("s1"), _step("s2")
        plan = _plan(s1, s2)

        async def fake_run(**kwargs):
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        self.assertEqual(result["plan_steps_completed"], 2)
        self.assertEqual(result["status"], "completed")

    def test_execute_plan_parallel_pending_approval_stops(self) -> None:
        """If layer 1 returns pending_approval, layer 2 must not execute."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])  # depends on s1 → layer 2
        plan = _plan(s1, s2)

        call_count = {"n": 0}

        async def fake_run(**kwargs):
            call_count["n"] += 1
            return {
                "final_message": "waiting",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "pending_approval",
                "approval_id": "appr-123",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        # Only s1 should run; s2 should not
        self.assertEqual(call_count["n"], 1)
        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["approval_id"], "appr-123")

    def test_execute_plan_parallel_step_failure_continues(self) -> None:
        """If a step raises an exception, the result is recorded as failed but other steps continue."""
        s1 = _step("s1")
        s2 = _step("s2")  # no deps, same layer
        plan = _plan(s1, s2)

        async def fake_run(**kwargs):
            # s1 sub-session raises, s2 succeeds
            sub = kwargs.get("session_id", "")
            if "__step_s1" in sub:
                raise RuntimeError("s1 exploded")
            return {
                "final_message": "s2 ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        # The overall run should not raise
        self.assertIn(result["status"], ("completed", "failed"))
        self.assertEqual(result["final_message"], "s2 ok")

    def test_execute_plan_parallel_collects_tool_calls(self) -> None:
        """tool_calls from multiple steps should all appear in the aggregated result."""
        from packages.contracts.agent_schemas import ToolCallRecord

        s1, s2 = _step("s1"), _step("s2")
        plan = _plan(s1, s2)

        async def fake_run(**kwargs):
            return {
                "final_message": "ok",
                "tool_calls": [
                    ToolCallRecord(
                        tool_name="calc",
                        arguments={"expr": "1+1"},
                        status="success",
                        result="2",
                    )
                ],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        # Both s1 and s2 each contribute 1 tool call → 2 total
        self.assertEqual(len(result["tool_calls"]), 2)

    def test_execute_plan_parallel_sub_session_ids(self) -> None:
        """Each step must use a unique sub_session_id f'{session_id}__step_{step.id}'."""
        s1, s2 = _step("s1"), _step("s2")
        plan = _plan(s1, s2)

        seen_sessions: list[str] = []

        async def fake_run(**kwargs):
            seen_sessions.append(kwargs["session_id"])
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="main-session",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        self.assertIn("main-session__step_s1", seen_sessions)
        self.assertIn("main-session__step_s2", seen_sessions)

    def test_execute_plan_parallel_metrics(self) -> None:
        """record_parallel_steps should be called for each layer."""
        from packages.agent.perf_metrics import get_agent_perf_metrics

        s1, s2 = _step("s1"), _step("s2")  # same layer
        plan = _plan(s1, s2)

        async def fake_run(**kwargs):
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="metrics-tenant",
                session_id="s",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        metrics = get_agent_perf_metrics()
        # 1 layer with 2 steps → 2 parallel steps recorded
        with metrics._lock:
            recorded = metrics._parallel_steps.get("metrics-tenant", 0)
        self.assertEqual(recorded, 2)

    def test_execute_plan_parallel_multi_layer(self) -> None:
        """Linear 3-step plan (3 layers) should complete all steps."""
        s1 = _step("s1")
        s2 = _step("s2", ["s1"])
        s3 = _step("s3", ["s2"])
        plan = _plan(s1, s2, s3)

        async def fake_run(**kwargs):
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t",
                session_id="s",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        self.assertEqual(result["plan_steps_completed"], 3)
        self.assertEqual(result["status"], "completed")

    def test_execute_plan_parallel_returns_correct_keys(self) -> None:
        """Result dict must contain all expected keys."""
        s1 = _step("s1")
        plan = _plan(s1)

        async def fake_run(**kwargs):
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "mymodel",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t",
                session_id="s",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        required_keys = {
            "tenant_id",
            "session_id",
            "final_message",
            "tool_calls",
            "steps",
            "model",
            "status",
            "plan",
            "plan_steps_completed",
        }
        for k in required_keys:
            self.assertIn(k, result, f"missing key: {k}")

    def test_execute_plan_parallel_tool_calls_dict_format(self) -> None:
        """ToolCallRecord passed as dict should be converted correctly."""
        s1 = _step("s1")
        plan = _plan(s1)

        async def fake_run(**kwargs):
            return {
                "final_message": "ok",
                "tool_calls": [
                    {
                        "tool_name": "calc",
                        "arguments": {"expr": "2+2"},
                        "status": "success",
                        "result": "4",
                    }
                ],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t",
                session_id="s",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=None,
                run_agent_fn=fake_run,
            )
        )
        self.assertEqual(len(result["tool_calls"]), 1)
        from packages.contracts.agent_schemas import ToolCallRecord

        self.assertIsInstance(result["tool_calls"][0], ToolCallRecord)


# ---------------------------------------------------------------------------
# AgentPerfMetrics.record_parallel_steps tests
# ---------------------------------------------------------------------------


class TestRecordParallelSteps(unittest.TestCase):
    def setUp(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def test_record_parallel_steps_basic(self) -> None:
        """record_parallel_steps should accumulate counters."""
        from packages.agent.perf_metrics import get_agent_perf_metrics

        m = get_agent_perf_metrics()
        m.record_parallel_steps(tenant_id="t", steps=3)
        m.record_parallel_steps(tenant_id="t", steps=2)
        with m._lock:
            self.assertEqual(m._parallel_steps["t"], 5)

    def test_record_parallel_steps_zero_ignored(self) -> None:
        """Steps=0 should not increment counter."""
        from packages.agent.perf_metrics import get_agent_perf_metrics

        m = get_agent_perf_metrics()
        m.record_parallel_steps(tenant_id="t", steps=0)
        with m._lock:
            self.assertNotIn("t", m._parallel_steps)

    def test_prometheus_text_includes_parallel_counter(self) -> None:
        """prometheus_text() should include agent_plan_parallel_steps_total."""
        from packages.agent.perf_metrics import get_agent_perf_metrics

        m = get_agent_perf_metrics()
        m.record_parallel_steps(tenant_id="mytenant", steps=4)
        text = m.prometheus_text()
        self.assertIn("agent_plan_parallel_steps_total", text)
        self.assertIn("mytenant", text)


if __name__ == "__main__":
    unittest.main()
