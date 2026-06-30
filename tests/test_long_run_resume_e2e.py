#!/usr/bin/env python3
"""tests/test_long_run_resume_e2e.py — long_run create → execute → resume E2E (#169)."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.long_horizon import (  # noqa: E402
    LayerStepOutcome,
    checkpoint_task,
    create_long_run,
    execute_long_run_resume,
    get_long_run,
    get_long_run_store,
    record_layer_step_outcomes,
    reset_long_run_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402
from packages.platform import configure, reset_platform_for_tests  # noqa: E402
from packages.platform.testing import InMemoryPlatformPort, InMemoryPlatformSettings  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="resume e2e", steps=list(steps))


class TestLongRunResumeE2E(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()
        reset_platform_for_tests()
        settings = InMemoryPlatformSettings(plan_execution_backend="planner")
        configure(InMemoryPlatformPort(settings=settings))

    def tearDown(self) -> None:
        reset_platform_for_tests()
        reset_long_run_store_for_tests()

    def test_record_layer_then_checkpoint_persists_completed(self) -> None:
        plan = _plan(
            PlanStep(id="s1", description="one"),
            PlanStep(id="s2", description="two", depends_on=["s1"]),
        )
        task = _run(create_long_run(plan, "t1", "sess"))

        _run(
            record_layer_step_outcomes(
                task.task_id,
                outcomes=[
                    LayerStepOutcome(
                        step_id="s1",
                        status="completed",
                        sub_session_id="sess__step_s1",
                    )
                ],
            )
        )
        cp = _run(checkpoint_task(task.task_id))
        self.assertIsNotNone(cp)
        self.assertEqual(cp.step_states[0].status, "completed")

        reloaded = _run(get_long_run(task.task_id))
        assert reloaded is not None
        self.assertEqual(reloaded.step_states[0].status, "completed")
        self.assertEqual(reloaded.step_states[1].status, "pending")

    def test_execute_long_run_resume_skips_completed_step(self) -> None:
        plan = _plan(
            PlanStep(id="s1", description="one"),
            PlanStep(id="s2", description="two", depends_on=["s1"]),
        )
        task = _run(create_long_run(plan, "t1", "sess"))
        store = get_long_run_store()
        task.step_states[0].status = "completed"
        _run(store.update_step_states(task.task_id, task.step_states))
        _run(checkpoint_task(task.task_id))

        call_log: list[str] = []

        async def fake_run_agent(**kwargs: object) -> dict[str, object]:
            msgs = kwargs.get("new_messages") or []
            content = msgs[0]["content"] if msgs else ""
            call_log.append(str(content))
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        with patch("packages.agent.runner.run_agent", new=AsyncMock(side_effect=fake_run_agent)):
            result = _run(
                execute_long_run_resume(
                    task.task_id,
                    tenant_id="t1",
                    allowed_tools=(),
                    allowed_models=("m",),
                    model="m",
                    session_store=MagicMock(),
                )
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(call_log), 1)
        self.assertIn("s2", call_log[0])
        final_task = _run(get_long_run(task.task_id))
        assert final_task is not None
        self.assertEqual(final_task.status, "completed")
        self.assertEqual(final_task.step_states[1].status, "completed")


if __name__ == "__main__":
    unittest.main()
