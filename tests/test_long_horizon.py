#!/usr/bin/env python3
"""tests/test_long_horizon.py — Phase R R2 长程任务单测。

≥10 个测试用例，无外部依赖。
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Normal package imports — the real packages must be kept intact (the previous
# bootstrap registered fake empty namespace modules, breaking the
# packages.agent → packages.contracts chain under pytest collection).
from apps.gateway.agent.long_run_routes import router as long_run_router
from apps.gateway.tenants import TenantRecord
from packages.agent.long_horizon import (
    Checkpoint,
    LongRunTask,
    StepState,
    cancel_task,
    checkpoint_task,
    create_long_run,
    get_long_run,
    get_long_run_store,
    get_task_status,
    new_checkpoint_id,
    new_task_id,
    reset_long_run_store_for_tests,
    resume_task,
)
from packages.agent.planner import execute_plan_parallel
from packages.contracts.agent_schemas import AgentPlan, PlanStep


def _run_async(coro):
    return asyncio.run(coro)


def _step(sid: str, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(id=sid, description=f"step {sid}", depends_on=depends_on or [])


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


# ---------------------------------------------------------------------------
# TestStepState
# ---------------------------------------------------------------------------


class TestStepState(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def test_step_state_defaults(self) -> None:
        s = StepState(step_id="s1")
        self.assertEqual(s.step_id, "s1")
        self.assertEqual(s.status, "pending")
        self.assertIsNone(s.started_at)
        self.assertIsNone(s.completed_at)
        self.assertIsNone(s.sub_session_id)
        self.assertEqual(s.tool_calls_summary, [])
        self.assertIsNone(s.error)

    def test_step_state_to_dict(self) -> None:
        now = time.time()
        s = StepState(
            step_id="s2",
            status="completed",
            started_at=now - 5,
            completed_at=now,
            sub_session_id="sess__step_s2",
            tool_calls_summary=[{"tool": "calc", "result": "42"}],
            error=None,
        )
        d = s.to_dict()
        self.assertEqual(d["step_id"], "s2")
        self.assertEqual(d["status"], "completed")
        self.assertAlmostEqual(d["completed_at"], now, delta=0.01)
        self.assertEqual(d["tool_calls_summary"], [{"tool": "calc", "result": "42"}])

    def test_step_state_status_transitions(self) -> None:
        s = StepState(step_id="s3")
        s.status = "running"
        self.assertEqual(s.status, "running")
        s.status = "failed"
        self.assertEqual(s.status, "failed")
        s.error = "timeout"
        self.assertEqual(s.error, "timeout")


# ---------------------------------------------------------------------------
# TestCheckpoint
# ---------------------------------------------------------------------------


class TestCheckpoint(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def test_checkpoint_to_dict(self) -> None:
        now = time.time()
        ss = [StepState(step_id="s1", status="completed"), StepState(step_id="s2")]
        cp = Checkpoint(
            checkpoint_id="cp-001",
            task_id="task-001",
            step_states=ss,
            layer_index=1,
            created_at=now,
        )
        d = cp.to_dict()
        self.assertEqual(d["checkpoint_id"], "cp-001")
        self.assertEqual(d["task_id"], "task-001")
        self.assertEqual(d["layer_index"], 1)
        self.assertEqual(len(d["step_states"]), 2)
        self.assertEqual(d["step_states"][0]["status"], "completed")

    def test_checkpoint_serialization_roundtrip(self) -> None:
        now = time.time()
        ss = [StepState(step_id="sx", status="skipped")]
        cp = Checkpoint(
            checkpoint_id="cp-xyz",
            task_id="t-xyz",
            step_states=ss,
            layer_index=3,
            created_at=now,
        )
        d = cp.to_dict()
        self.assertEqual(d["layer_index"], 3)
        self.assertEqual(d["step_states"][0]["step_id"], "sx")
        self.assertAlmostEqual(d["created_at"], now, delta=0.01)


# ---------------------------------------------------------------------------
# TestLongRunTask
# ---------------------------------------------------------------------------


class TestLongRunTask(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def _make_task(self) -> LongRunTask:
        plan = _plan(_step("s1"), _step("s2"), _step("s3"))
        step_states = [StepState(step_id=s.id) for s in plan.steps]
        return LongRunTask(
            task_id="task-1",
            tenant_id="t1",
            session_id="sess1",
            plan=plan,
            step_states=step_states,
        )

    def test_task_to_dict(self) -> None:
        task = self._make_task()
        d = task.to_dict()
        self.assertEqual(d["task_id"], "task-1")
        self.assertEqual(d["tenant_id"], "t1")
        self.assertEqual(d["status"], "pending")
        self.assertEqual(len(d["step_states"]), 3)
        self.assertIn("plan", d)

    def test_progress_all_pending(self) -> None:
        task = self._make_task()
        p = task.progress()
        self.assertEqual(p["total"], 3)
        self.assertEqual(p["completed"], 0)
        self.assertEqual(p["percent"], 0.0)

    def test_progress_partial_completed(self) -> None:
        task = self._make_task()
        task.step_states[0].status = "completed"
        task.step_states[1].status = "completed"
        p = task.progress()
        self.assertEqual(p["completed"], 2)
        self.assertAlmostEqual(p["percent"], 66.7, delta=0.1)

    def test_progress_all_completed(self) -> None:
        task = self._make_task()
        for s in task.step_states:
            s.status = "completed"
        p = task.progress()
        self.assertEqual(p["completed"], 3)
        self.assertEqual(p["percent"], 100.0)

    def test_progress_with_failed(self) -> None:
        task = self._make_task()
        task.step_states[0].status = "completed"
        task.step_states[1].status = "failed"
        p = task.progress()
        self.assertEqual(p["failed"], 1)
        self.assertEqual(p["completed"], 1)


# ---------------------------------------------------------------------------
# TestLongRunTaskStore
# ---------------------------------------------------------------------------


class TestLongRunTaskStore(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()
        self.store = get_long_run_store()

    def _plan(self) -> AgentPlan:
        return _plan(_step("s1"), _step("s2"))

    def test_create_and_get(self) -> None:
        plan = self._plan()
        task = _run_async(self.store.create(plan, "tenant1", "sess1"))
        self.assertIsNotNone(task.task_id)
        fetched = _run_async(self.store.get(task.task_id))
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.tenant_id, "tenant1")

    def test_get_missing_returns_none(self) -> None:
        self.assertIsNone(_run_async(self.store.get("nonexistent")))

    def test_list_by_tenant(self) -> None:
        plan = self._plan()
        t1 = _run_async(self.store.create(plan, "tenantA"))
        t2 = _run_async(self.store.create(plan, "tenantA"))
        _run_async(self.store.create(plan, "tenantB"))
        tasks = _run_async(self.store.list_by_tenant("tenantA"))
        ids = {t.task_id for t in tasks}
        self.assertIn(t1.task_id, ids)
        self.assertIn(t2.task_id, ids)
        self.assertEqual(len(tasks), 2)

    def test_update_status(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        ok = _run_async(self.store.update_status(task.task_id, "running"))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.status, "running")

    def test_update_status_invalid(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        ok = _run_async(self.store.update_status(task.task_id, "INVALID_STATUS"))
        self.assertFalse(ok)

    def test_add_checkpoint_and_get_latest(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        cp = Checkpoint(
            checkpoint_id="cp1",
            task_id=task.task_id,
            step_states=[StepState(step_id="s1", status="completed")],
            layer_index=1,
            created_at=time.time(),
        )
        ok = _run_async(self.store.add_checkpoint(task.task_id, cp))
        self.assertTrue(ok)
        latest = _run_async(self.store.get_latest_checkpoint(task.task_id))
        self.assertIsNotNone(latest)
        self.assertEqual(latest.checkpoint_id, "cp1")

    def test_get_latest_checkpoint_no_checkpoints(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        self.assertIsNone(_run_async(self.store.get_latest_checkpoint(task.task_id)))

    def test_cancel(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        ok = _run_async(self.store.cancel(task.task_id))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.status, "cancelled")

    def test_cancel_already_cancelled(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        _run_async(self.store.cancel(task.task_id))
        ok = _run_async(self.store.cancel(task.task_id))
        self.assertFalse(ok)

    def test_delete(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        ok = _run_async(self.store.delete(task.task_id))
        self.assertTrue(ok)
        self.assertIsNone(_run_async(self.store.get(task.task_id)))

    def test_set_final_result(self) -> None:
        task = _run_async(self.store.create(self._plan(), "t1"))
        ok = _run_async(self.store.set_final_result(task.task_id, {"key": "value"}))
        self.assertTrue(ok)
        updated = _run_async(self.store.get(task.task_id))
        self.assertEqual(updated.final_result, {"key": "value"})


# ---------------------------------------------------------------------------
# TestResumeTask
# ---------------------------------------------------------------------------


class TestResumeTask(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()
        self.store = get_long_run_store()

    def test_resume_from_checkpoint(self) -> None:
        plan = _plan(_step("s1"), _step("s2", ["s1"]))
        task = _run_async(create_long_run(plan, "t1", "sess1"))
        # Complete s1 and create checkpoint
        task.step_states[0].status = "completed"
        _run_async(self.store.update_step_states(task.task_id, task.step_states))
        _run_async(self.store.update_status(task.task_id, "paused"))
        _run_async(checkpoint_task(task.task_id))

        # Now resume
        resumed = _run_async(resume_task(task.task_id))
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.status, "running")
        self.assertEqual(resumed.step_states[0].status, "completed")
        self.assertEqual(resumed.step_states[1].status, "pending")

    def test_resume_without_checkpoint_starts_fresh(self) -> None:
        plan = _plan(_step("s1"), _step("s2"))
        task = _run_async(create_long_run(plan, "t1"))
        _run_async(self.store.update_status(task.task_id, "paused"))

        resumed = _run_async(resume_task(task.task_id))
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.status, "running")
        # No checkpoint, all still pending
        for ss in resumed.step_states:
            self.assertEqual(ss.status, "pending")

    def test_resume_nonexistent_task(self) -> None:
        result = _run_async(resume_task("nonexistent-task-id"))
        self.assertIsNone(result)

    def test_checkpoint_captures_current_state(self) -> None:
        plan = _plan(_step("s1"), _step("s2"), _step("s3"))
        task = _run_async(create_long_run(plan, "t1"))
        task.step_states[0].status = "completed"
        task.step_states[1].status = "completed"
        _run_async(self.store.update_step_states(task.task_id, task.step_states))

        cp = _run_async(checkpoint_task(task.task_id))
        self.assertIsNotNone(cp)
        self.assertEqual(cp.layer_index, 2)
        statuses = [s.status for s in cp.step_states]
        self.assertEqual(statuses, ["completed", "completed", "pending"])


# ---------------------------------------------------------------------------
# TestGetTaskStatus
# ---------------------------------------------------------------------------


class TestGetTaskStatus(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def test_get_task_status_returns_combined(self) -> None:
        plan = _plan(_step("s1"), _step("s2"))
        task = _run_async(create_long_run(plan, "t1"))
        status = _run_async(get_task_status(task.task_id))
        self.assertIsNotNone(status)
        self.assertIn("progress", status)
        self.assertIn("task_id", status)
        self.assertEqual(status["progress"]["total"], 2)

    def test_get_task_status_missing(self) -> None:
        self.assertIsNone(_run_async(get_task_status("no-such-id")))


# ---------------------------------------------------------------------------
# TestExecutePlanParallelLongRun
# ---------------------------------------------------------------------------


class TestExecutePlanParallelLongRun(unittest.TestCase):
    """验证 execute_plan_parallel 的 long_run_task_id 集成：跳过已完成 step，auto-checkpoint。"""

    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def test_skip_completed_steps(self) -> None:
        """已在 long-run store 中标记为 completed 的 step 应被跳过（不重新执行）。"""
        plan = _plan(_step("s1"), _step("s2", ["s1"]))

        # Create task and mark s1 as completed
        task = _run_async(create_long_run(plan, "t1", "sess1"))
        store = get_long_run_store()
        task.step_states[0].status = "completed"
        _run_async(store.update_step_states(task.task_id, task.step_states))

        call_log: list[str] = []

        async def mock_runner(**kwargs: object) -> dict:
            msgs = kwargs.get("new_messages", [])
            content = msgs[0]["content"] if msgs else ""
            call_log.append(content)
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4o",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=("calc",),
                allowed_models=("gpt-4o",),
                model="gpt-4o",
                session_store=None,
                run_agent_fn=mock_runner,
                long_run_task_id=task.task_id,
            )
        )

        # s1 is completed → only s2 should be called
        self.assertEqual(len(call_log), 1, f"期望只调用 1 次(s2), 实际: {call_log}")
        self.assertIn("s2", call_log[0])
        self.assertEqual(result["status"], "completed")

    def test_auto_checkpoint_triggered_after_layer(self) -> None:
        """完成一层后应自动调用 checkpoint_task。"""
        plan = _plan(_step("s1"))
        task = _run_async(create_long_run(plan, "t1", "sess1"))

        async def mock_runner(**kwargs: object) -> dict:
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4o",
                "status": "completed",
            }

        _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=("calc",),
                allowed_models=("gpt-4o",),
                model="gpt-4o",
                session_store=None,
                run_agent_fn=mock_runner,
                long_run_task_id=task.task_id,
            )
        )

        # Auto-checkpoint should have been saved
        updated = _run_async(get_long_run(task.task_id))
        self.assertIsNotNone(updated)
        self.assertGreaterEqual(len(updated.checkpoints), 1)

    def test_no_long_run_task_id_still_works(self) -> None:
        """不传 long_run_task_id 时，execute_plan_parallel 正常执行（向后兼容）。"""
        plan = _plan(_step("s1"))

        async def mock_runner(**kwargs: object) -> dict:
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4o",
                "status": "completed",
            }

        result = _run_async(
            execute_plan_parallel(
                plan=plan,
                tenant_id="t1",
                session_id="sess1",
                allowed_tools=(),
                allowed_models=("gpt-4o",),
                model="gpt-4o",
                session_store=None,
                run_agent_fn=mock_runner,
            )
        )
        self.assertEqual(result["status"], "completed")


# ---------------------------------------------------------------------------
# TestLongRunRoutes — FastAPI TestClient
# ---------------------------------------------------------------------------


class TestLongRunRoutes(unittest.TestCase):
    def setUp(self) -> None:
        reset_long_run_store_for_tests()

    def _make_app(self):
        """Build a FastAPI app with the long_run router (real packages, no stubs)."""
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(long_run_router)
        return app

    def _make_tenants(self) -> dict:
        return {
            "tenant1": TenantRecord(
                tenant_id="tenant1",
                bearer_token="tok-test",
                daily_request_quota=-1,
                allowed_models=("gpt-4o",),
                allowed_tools=("calc",),
                default_model="gpt-4o",
                rate_limit_rps=100.0,
                rate_limit_burst=100,
                token_budget_daily=-1,
                token_budget_monthly=-1,
                role="user",
            )
        }

    def _headers(self) -> dict:
        return {"X-Tenant-Id": "tenant1", "Authorization": "Bearer tok-test"}

    def _plan_payload(self) -> dict:
        return {
            "plan": {
                "goal": "test goal",
                "steps": [
                    {"id": "s1", "description": "step 1", "depends_on": []},
                    {"id": "s2", "description": "step 2", "depends_on": ["s1"]},
                ],
            },
            "session_id": "sess-test",
        }

    def _with_tenants(self, tenants):
        """Context helper: patch load_tenants in the route module."""
        route_mod = sys.modules.get("apps.gateway.agent.long_run_routes")
        return patch.object(route_mod, "load_tenants", return_value=tenants)

    def test_post_create_task(self) -> None:
        from fastapi.testclient import TestClient

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.post(
                "/v1/agent/long-run",
                json=self._plan_payload(),
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertIn("task_id", data)
        self.assertEqual(data["status"], "pending")

    def test_get_task(self) -> None:
        from fastapi.testclient import TestClient

        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"), _step("s2", ["s1"]))
        task = _run_async(create_long_run(plan, "tenant1", "sess-get"))

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.get(
                f"/v1/agent/long-run/{task.task_id}",
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["task_id"], task.task_id)
        self.assertIn("progress", data)

    def test_get_task_not_found(self) -> None:
        from fastapi.testclient import TestClient

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.get("/v1/agent/long-run/no-such-id", headers=self._headers())
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_list_tasks(self) -> None:
        from fastapi.testclient import TestClient

        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"))
        _run_async(create_long_run(plan, "tenant1"))
        _run_async(create_long_run(plan, "tenant1"))

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.get("/v1/agent/long-run", headers=self._headers())
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total"], 2)

    def test_resume_task(self) -> None:
        from fastapi.testclient import TestClient

        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"))
        task = _run_async(create_long_run(plan, "tenant1"))
        _run_async(get_long_run_store().update_status(task.task_id, "paused"))

        app = self._make_app()
        tenants = self._make_tenants()

        async def _mock_resume(*_args, **_kwargs):
            await get_long_run_store().update_status(task.task_id, "completed")
            return {
                "status": "completed",
                "task_id": task.task_id,
                "long_run_status": "completed",
                "progress": {"completed": 1, "total": 1, "percent": 100},
                "plan_steps_completed": 1,
                "final_message": "done",
            }

        mock_resume = AsyncMock(side_effect=_mock_resume)
        route_mod = sys.modules.get("apps.gateway.agent.long_run_routes")
        session_mod = types.ModuleType("packages.agent.session")
        session_mod.get_session_store = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
        with self._with_tenants(tenants):
            with patch.object(route_mod, "execute_long_run_resume", mock_resume):
                with patch.dict(sys.modules, {"packages.agent.session": session_mod}):
                    client = TestClient(app)
                    resp = client.post(
                        f"/v1/agent/long-run/{task.task_id}/resume",
                        headers=self._headers(),
                    )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["plan_status"], "completed")
        mock_resume.assert_awaited_once()

    def test_cancel_task(self) -> None:
        from fastapi.testclient import TestClient

        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"))
        task = _run_async(create_long_run(plan, "tenant1"))

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.post(
                f"/v1/agent/long-run/{task.task_id}/cancel",
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "cancelled")

    def test_resume_completed_task_returns_409(self) -> None:
        from fastapi.testclient import TestClient

        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"))
        task = _run_async(create_long_run(plan, "tenant1"))
        _run_async(get_long_run_store().update_status(task.task_id, "completed"))

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.post(
                f"/v1/agent/long-run/{task.task_id}/resume",
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_unauthorized_returns_401(self) -> None:
        from fastapi.testclient import TestClient

        app = self._make_app()
        tenants = self._make_tenants()
        with self._with_tenants(tenants):
            client = TestClient(app)
            resp = client.get("/v1/agent/long-run")
        self.assertEqual(resp.status_code, 401, resp.text)


# ---------------------------------------------------------------------------
# TestUtilFunctions
# ---------------------------------------------------------------------------


class TestUtilFunctions(unittest.TestCase):
    def test_new_task_id_unique(self) -> None:
        ids = {new_task_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_new_checkpoint_id_unique(self) -> None:
        ids = {new_checkpoint_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_cancel_convenience_fn(self) -> None:
        reset_long_run_store_for_tests()
        plan = _plan(_step("s1"))
        task = _run_async(create_long_run(plan, "t1"))
        ok = _run_async(cancel_task(task.task_id))
        self.assertTrue(ok)
        self.assertEqual(_run_async(get_long_run(task.task_id)).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
