#!/usr/bin/env python3
"""tests/test_execution_handle.py — #169 PR-2 ExecutionHandle 只读状态 API。"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.execution_handle import (  # noqa: E402
    get_execution_handle_status,
    get_orchestrator_handle_status,
    get_plan_approval_handle_status,
    parse_execution_handle_lookup,
)
from packages.agent.graph_checkpoint import (  # noqa: E402
    InMemoryGraphCheckpointStore,
    WorkflowExecutionCheckpoint,
)
from packages.agent.long_horizon import (  # noqa: E402
    create_long_run,
    get_long_run_store,
    reset_long_run_store_for_tests,
)
from packages.agent.plan_approval import (  # noqa: E402
    approve_plan,
    new_plan_approval_id,
    reset_plan_approval_store_for_tests,
    store_plan_approval,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _step(sid: str) -> PlanStep:
    return PlanStep(id=sid, description=f"step {sid}")


def _plan(*steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal="test goal", steps=list(steps))


class TestParseExecutionHandleLookup(unittest.TestCase):
    def test_requires_exactly_one(self) -> None:
        self.assertIsInstance(parse_execution_handle_lookup(), str)
        err = parse_execution_handle_lookup(
            plan_approval_id="a",
            task_id="b",
        )
        self.assertIsInstance(err, str)

    def test_parses_each_layer(self) -> None:
        self.assertEqual(
            parse_execution_handle_lookup(plan_approval_id=" pa1 "),
            ("plan_approval", "pa1"),
        )
        self.assertEqual(
            parse_execution_handle_lookup(execution_id="ex1"),
            ("orchestrator", "ex1"),
        )
        self.assertEqual(
            parse_execution_handle_lookup(task_id="t1"),
            ("long_run", "t1"),
        )


class TestExecutionHandleStatus(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()
        reset_long_run_store_for_tests()

    def test_plan_approval_approved_resumable(self) -> None:
        aid = new_plan_approval_id()
        store_plan_approval(aid, _plan(_step("s1")), tenant_id="t1")
        approve_plan(aid)
        status = get_plan_approval_handle_status(aid, tenant_id="t1")
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.layer, "plan_approval")
        self.assertTrue(status.resumable)
        self.assertEqual(status.resume_hint.path, "/v1/agent/run")

    def test_plan_approval_wrong_tenant_hidden(self) -> None:
        aid = new_plan_approval_id()
        store_plan_approval(aid, _plan(_step("s1")), tenant_id="t1")
        self.assertIsNone(get_plan_approval_handle_status(aid, tenant_id="other"))

    def test_orchestrator_failed_resumable(self) -> None:
        store = InMemoryGraphCheckpointStore()
        cp = WorkflowExecutionCheckpoint(
            execution_id="e1",
            tenant_id="t1",
            workflow_id="wf1",
            status="failed",
            current_node="n1",
            error="boom",
        )
        store.save(cp)
        status = get_orchestrator_handle_status("e1", tenant_id="t1", checkpoint_store=store)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertTrue(status.resumable)
        self.assertIn("/internal/orchestrator/executions/e1/resume", status.resume_hint.path)

    def test_orchestrator_completed_not_resumable(self) -> None:
        store = InMemoryGraphCheckpointStore()
        cp = WorkflowExecutionCheckpoint(
            execution_id="e2",
            tenant_id="t1",
            workflow_id="wf1",
            status="completed",
            current_node=None,
        )
        store.save(cp)
        status = get_orchestrator_handle_status("e2", tenant_id="t1", checkpoint_store=store)
        assert status is not None
        self.assertFalse(status.resumable)

    def test_long_run_paused_resumable(self) -> None:
        task = _run_async(create_long_run(_plan(_step("s1")), tenant_id="t1"))
        _run_async(get_long_run_store().update_status(task.task_id, "paused"))
        status = _run_async(get_execution_handle_status("long_run", task.task_id, tenant_id="t1"))
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.layer, "long_run")
        self.assertTrue(status.resumable)
        self.assertIn(f"/v1/agent/long-run/{task.task_id}/resume", status.resume_hint.path)


class TestExecutionHandleRoutes(unittest.TestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()
        reset_long_run_store_for_tests()

    def _make_app(self):
        from fastapi import FastAPI

        settings_stub = MagicMock()
        settings_stub.auth_jwt_enabled = False
        settings_stub.auth_jwt_secret = None
        settings_stub.redis_url = None
        settings_mod = types.ModuleType("apps.gateway.settings")
        settings_mod.get_settings = lambda: settings_stub  # type: ignore[attr-defined]
        sys.modules["apps.gateway.settings"] = settings_mod

        route_mod_path = REPO_ROOT / "apps" / "gateway" / "agent" / "execution_handle_routes.py"
        spec = importlib.util.spec_from_file_location(
            "apps.gateway.agent.execution_handle_routes",
            route_mod_path,
        )
        assert spec and spec.loader
        route_mod = importlib.util.module_from_spec(spec)
        sys.modules["apps.gateway.agent.execution_handle_routes"] = route_mod
        spec.loader.exec_module(route_mod)

        app = FastAPI()
        app.include_router(route_mod.router)
        return app

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer tok-test", "X-Tenant-Id": "tenant1"}

    def test_route_plan_approval_status(self) -> None:
        from fastapi.testclient import TestClient

        aid = new_plan_approval_id()
        store_plan_approval(aid, _plan(_step("s1")), tenant_id="tenant1")

        tenants = {
            "tenant1": MagicMock(
                tenant_id="tenant1",
                bearer_token="tok-test",
                role="user",
            )
        }
        app = self._make_app()
        route_mod = sys.modules["apps.gateway.agent.execution_handle_routes"]
        with patch.object(route_mod, "load_tenants", return_value=tenants):
            client = TestClient(app)
            resp = client.get(
                "/v1/agent/execution-status",
                params={"plan_approval_id": aid},
                headers=self._headers(),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["layer"], "plan_approval")
        self.assertEqual(data["handle_id"], aid)
        self.assertEqual(data["status"], "pending")

    def test_route_missing_handle_400(self) -> None:
        from fastapi.testclient import TestClient

        tenants = {
            "tenant1": MagicMock(
                tenant_id="tenant1",
                bearer_token="tok-test",
                role="user",
            )
        }
        app = self._make_app()
        route_mod = sys.modules["apps.gateway.agent.execution_handle_routes"]
        with patch.object(route_mod, "load_tenants", return_value=tenants):
            client = TestClient(app)
            resp = client.get("/v1/agent/execution-status", headers=self._headers())
        self.assertEqual(resp.status_code, 400, resp.text)


if __name__ == "__main__":
    unittest.main()
