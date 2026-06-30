#!/usr/bin/env python3
"""tests/test_long_horizon.py — Phase R R2 长程任务单测。

≥10 个测试用例，无外部依赖。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _ensure_namespace(name: str) -> types.ModuleType:
    """确保一个真实的 namespace module 注册到 sys.modules（若不存在）。"""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Bootstrap: register namespace packages so importlib chains work
# ---------------------------------------------------------------------------
_ensure_namespace("packages")
_ensure_namespace("packages.contracts")
_ensure_namespace("packages.agent")
_ensure_namespace("apps")
_ensure_namespace("apps.gateway")
_ensure_namespace("apps.gateway.agent")

# Load real contracts.errors (needed by http_utils)
for _mod_name in [
    "packages.observability",
    "packages.observability.context",
    "packages.auth",
    "packages.auth.rbac",
    "packages.auth.jwt_hs256",
]:
    _ensure_namespace(_mod_name)

# Stub observability.context.get_trace_id
sys.modules["packages.observability.context"].get_trace_id = lambda: "test-trace"  # type: ignore[attr-defined]

# Stub rbac
sys.modules["packages.auth.rbac"].can_patch_tenant_limits = lambda role: role == "platform_admin"  # type: ignore[attr-defined]

# Stub contracts.errors (avoids Python 3.9 | union syntax in that file)
_errors_mod = types.ModuleType("packages.contracts.errors")


class _ErrorDetail:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def model_dump(self):
        return self.__dict__


class _ErrorBody:
    def __init__(self, error=None):
        self.error = error

    def model_dump(self):
        return {"error": self.error.model_dump() if self.error else None}


_errors_mod.ErrorDetail = _ErrorDetail  # type: ignore[attr-defined]
_errors_mod.ErrorBody = _ErrorBody  # type: ignore[attr-defined]
sys.modules["packages.contracts.errors"] = _errors_mod

_agent_schemas = _load_module(
    "packages.contracts.agent_schemas",
    str(REPO_ROOT / "packages" / "contracts" / "agent_schemas.py"),
)
_load_module(
    "packages.contracts.tenant",
    str(REPO_ROOT / "packages" / "contracts" / "tenant.py"),
)
_ensure_namespace("packages.tenant")
_load_module(
    "packages.tenant.loader",
    str(REPO_ROOT / "packages" / "tenant" / "loader.py"),
)

# Now load long_horizon directly (bypasses packages.agent.__init__)
_long_horizon = _load_module(
    "packages.agent.long_horizon",
    str(REPO_ROOT / "packages" / "agent" / "long_horizon.py"),
)

# Import symbols from the loaded modules
AgentPlan = _agent_schemas.AgentPlan
PlanStep = _agent_schemas.PlanStep

StepState = _long_horizon.StepState
Checkpoint = _long_horizon.Checkpoint
LongRunTask = _long_horizon.LongRunTask
LongRunTaskStore = _long_horizon.LongRunTaskStore
get_long_run_store = _long_horizon.get_long_run_store
reset_long_run_store_for_tests = _long_horizon.reset_long_run_store_for_tests
create_long_run = _long_horizon.create_long_run
get_long_run = _long_horizon.get_long_run
checkpoint_task = _long_horizon.checkpoint_task
resume_task = _long_horizon.resume_task
cancel_task = _long_horizon.cancel_task
get_task_status = _long_horizon.get_task_status
new_task_id = _long_horizon.new_task_id
new_checkpoint_id = _long_horizon.new_checkpoint_id


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

    def _load_planner(self):
        """动态加载 planner 模块，绕过 packages.agent.__init__ 链。"""
        from packages.platform import configure, reset_platform_for_tests
        from packages.platform.testing import InMemoryPlatformPort

        reset_platform_for_tests()
        configure(InMemoryPlatformPort())

        for mod_name in [
            "packages.agent.perf_metrics",
            "packages.agent.registry",
            "packages.observability.otel",
        ]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = MagicMock()

        perf_mod = sys.modules["packages.agent.perf_metrics"]
        perf_mock = MagicMock()
        perf_mock.record_parallel_steps = MagicMock()
        perf_mod.get_agent_perf_metrics = lambda: perf_mock

        # Also stub packages.agent.runner so the lazy import inside the function works
        if "packages.agent.runner" not in sys.modules:
            sys.modules["packages.agent.runner"] = MagicMock()

        return _load_module(
            "packages.agent.planner",
            str(REPO_ROOT / "packages" / "agent" / "planner.py"),
        )

    def test_skip_completed_steps(self) -> None:
        """已在 long-run store 中标记为 completed 的 step 应被跳过（不重新执行）。"""
        planner = self._load_planner()
        execute_plan_parallel = planner.execute_plan_parallel

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

        settings_mock = MagicMock()
        settings_mock.agent_model = "gpt-4o"
        settings_mock.default_model = "gpt-4o"
        settings_mock.plan_execution_mode = "parallel"

        perf_mock = MagicMock()
        perf_mock.record_parallel_steps = MagicMock()

        with (
            patch.object(planner, "get_settings", return_value=settings_mock),
            patch.object(planner, "get_agent_perf_metrics", return_value=perf_mock),
        ):
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
        planner = self._load_planner()
        execute_plan_parallel = planner.execute_plan_parallel

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

        settings_mock = MagicMock()
        settings_mock.agent_model = "gpt-4o"
        settings_mock.default_model = "gpt-4o"
        settings_mock.plan_execution_mode = "parallel"

        perf_mock = MagicMock()
        perf_mock.record_parallel_steps = MagicMock()

        with (
            patch.object(planner, "get_settings", return_value=settings_mock),
            patch.object(planner, "get_agent_perf_metrics", return_value=perf_mock),
        ):
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
        planner = self._load_planner()
        execute_plan_parallel = planner.execute_plan_parallel

        plan = _plan(_step("s1"))

        async def mock_runner(**kwargs: object) -> dict:
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4o",
                "status": "completed",
            }

        settings_mock = MagicMock()
        settings_mock.agent_model = "gpt-4o"
        settings_mock.default_model = "gpt-4o"

        perf_mock = MagicMock()
        perf_mock.record_parallel_steps = MagicMock()

        with (
            patch.object(planner, "get_settings", return_value=settings_mock),
            patch.object(planner, "get_agent_perf_metrics", return_value=perf_mock),
        ):
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
        """Build a FastAPI app with the long_run router, loading deps manually."""
        from fastapi import FastAPI

        # Stub settings so resolve_tenant's internal `from apps.gateway.settings import get_settings` works
        settings_stub = MagicMock()
        settings_stub.auth_jwt_enabled = False
        settings_stub.auth_jwt_secret = None
        settings_mod = types.ModuleType("apps.gateway.settings")
        settings_mod.get_settings = lambda: settings_stub  # type: ignore[attr-defined]
        sys.modules["apps.gateway.settings"] = settings_mod

        for mod_name in ["packages.observability.otel"]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = MagicMock()

        # Load tenants first (http_utils depends on it)
        if "apps.gateway.tenants" not in sys.modules:
            _load_module(
                "apps.gateway.tenants",
                str(REPO_ROOT / "apps" / "gateway" / "tenants.py"),
            )

        # Load http_utils (needs tenants, contracts.errors, observability)
        if "apps.gateway.http_utils" not in sys.modules:
            _load_module(
                "apps.gateway.http_utils",
                str(REPO_ROOT / "apps" / "gateway" / "http_utils.py"),
            )

        # Reload long_run_routes fresh to get the router
        route_mod = _load_module(
            "apps.gateway.agent.long_run_routes",
            str(REPO_ROOT / "apps" / "gateway" / "agent" / "long_run_routes.py"),
        )

        app = FastAPI()
        app.include_router(route_mod.router)
        return app

    def _make_tenants(self) -> dict:
        from apps.gateway.tenants import TenantRecord

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

    def _get_settings_mock(self):
        m = MagicMock()
        m.auth_jwt_enabled = False
        m.auth_jwt_secret = None
        return m

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
