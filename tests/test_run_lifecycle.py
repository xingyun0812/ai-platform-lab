"""run_lifecycle — self_evolve 写路径 hook 单测。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from packages.agent.run_lifecycle import (
    extract_tool_calls_for_self_evolve,
    finalize_agent_run_result,
    is_terminal_run_status,
    outcome_from_run_status,
    schedule_self_evolve_after_run,
    self_evolve_enabled,
)


class TestRunLifecycleHelpers(unittest.TestCase):
    def test_terminal_statuses(self) -> None:
        self.assertTrue(is_terminal_run_status("completed"))
        self.assertTrue(is_terminal_run_status("failed"))
        self.assertFalse(is_terminal_run_status("pending_approval"))
        self.assertFalse(is_terminal_run_status("pending_plan_approval"))

    def test_outcome_mapping(self) -> None:
        self.assertEqual(outcome_from_run_status("completed"), "success")
        self.assertEqual(outcome_from_run_status("failed"), "failed")

    def test_extract_tool_calls_dict_and_objects(self) -> None:
        class _Rec:
            def to_dict(self) -> dict:
                return {"tool": "calc"}

        out = extract_tool_calls_for_self_evolve([{"a": 1}, _Rec()])
        self.assertEqual(out, [{"a": 1}, {"tool": "calc"}])


class TestScheduleSelfEvolve(unittest.IsolatedAsyncioTestCase):
    async def test_schedules_task_on_completed(self) -> None:
        trigger = AsyncMock(return_value={"experience_id": "exp1"})
        with patch.dict("os.environ", {"SELF_EVOLVE_ENABLED": "true"}):
            with patch(
                "packages.agent.self_evolve.trigger_self_evolve",
                trigger,
            ):
                schedule_self_evolve_after_run(
                    result={"status": "completed", "tool_calls": []},
                    tenant_id="t1",
                    model="chat-fast",
                    plan=None,
                )
                await asyncio.sleep(0)
        trigger.assert_awaited_once()
        self.assertEqual(trigger.await_args.kwargs["tenant_id"], "t1")

    async def test_skips_pending_approval(self) -> None:
        trigger = AsyncMock()
        with patch(
            "packages.agent.self_evolve.trigger_self_evolve",
            trigger,
        ):
            schedule_self_evolve_after_run(
                result={"status": "pending_approval"},
                tenant_id="t1",
            )
            await asyncio.sleep(0)
        trigger.assert_not_awaited()

    async def test_respects_disable_env(self) -> None:
        trigger = AsyncMock()
        with patch.dict("os.environ", {"SELF_EVOLVE_ENABLED": "false"}):
            self.assertFalse(self_evolve_enabled())
            with patch(
                "packages.agent.self_evolve.trigger_self_evolve",
                trigger,
            ):
                schedule_self_evolve_after_run(
                    result={"status": "completed"},
                    tenant_id="t1",
                )
                await asyncio.sleep(0)
        trigger.assert_not_awaited()

    async def test_finalize_returns_same_payload(self) -> None:
        payload = {"status": "completed", "final_message": "ok"}
        with patch(
            "packages.agent.run_lifecycle.schedule_self_evolve_after_run",
        ) as schedule:
            out = finalize_agent_run_result(
                payload,
                tenant_id="t2",
                model="m1",
            )
        self.assertIs(out, payload)
        schedule.assert_called_once()


if __name__ == "__main__":
    unittest.main()
