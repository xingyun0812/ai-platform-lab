"""最小 LangGraph 等价物 — graph_runtime / checkpoint 单测。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.agent.graph_checkpoint import (
    get_graph_checkpoint_store,
    reset_graph_checkpoint_store_for_tests,
)
from packages.agent.graph_runtime import GraphRuntimeError, execute_agent_graph
from packages.agent.plan_approval import (
    approve_plan,
    reset_plan_approval_store_for_tests,
    store_plan_approval,
)
from packages.agent.planner import parse_plan
from packages.contracts.agent_schemas import AgentPlan, AgentRunRequest, PlanStep

SAMPLE_PLAN = AgentPlan(
    goal="resume test",
    steps=[PlanStep(id="s1", description="calc 1+1", tool_hint="calc", depends_on=[])],
)


class TestPlanApprovalResume(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_plan_approval_store_for_tests()

    async def test_resume_approved_plan(self) -> None:
        aid = "test-plan-approval-id"
        store_plan_approval(aid, SAMPLE_PLAN, tenant_id="admin", session_id="sess1")
        approve_plan(aid)

        mock_execute = AsyncMock(
            return_value={
                "tenant_id": "admin",
                "session_id": "sess1",
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "chat-fast",
                "status": "completed",
                "plan": SAMPLE_PLAN,
                "plan_steps_completed": 1,
            }
        )
        settings = MagicMock()
        settings.plan_execution_mode = "parallel"
        settings.plan_max_replan_attempts = 2
        tenant = MagicMock()
        tenant.tenant_id = "admin"
        tenant.allowed_tools = ()
        tenant.allowed_models = ()

        body = AgentRunRequest(
            tenant_id="admin",
            session_id="sess1",
            plan_approval_id=aid,
            model="chat-fast",
        )

        with patch("packages.agent.graph_runtime.get_plan_executor", return_value=mock_execute):
            result = await execute_agent_graph(
                body=body,
                tenant=tenant,
                settings=settings,
                session_store=MagicMock(),
                new_messages=[],
                step_system_messages=None,
                shadow_mode=False,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resumed_from_plan_approval_id"], aid)
        mock_execute.assert_awaited_once()
        self.assertFalse(mock_execute.await_args.kwargs.get("require_plan_approval"))

    async def test_resume_pending_raises(self) -> None:
        aid = "pending-id"
        store_plan_approval(aid, SAMPLE_PLAN, tenant_id="admin", session_id="sess1")
        body = AgentRunRequest(
            tenant_id="admin",
            session_id="sess1",
            plan_approval_id=aid,
        )
        tenant = MagicMock(tenant_id="admin", allowed_tools=(), allowed_models=())
        settings = MagicMock()

        with self.assertRaises(GraphRuntimeError) as ctx:
            await execute_agent_graph(
                body=body,
                tenant=tenant,
                settings=settings,
                session_store=MagicMock(),
                new_messages=[],
                step_system_messages=None,
                shadow_mode=False,
            )
        self.assertEqual(ctx.exception.code, "PLAN_APPROVAL_PENDING")


class TestWorkflowCheckpoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_graph_checkpoint_store_for_tests()

    async def test_checkpoint_create_and_get(self) -> None:
        from packages.agent.orchestrator.checkpoint_engine import execute_workflow_checkpointed
        from packages.agent.orchestrator.graph import Workflow, GraphNode, GraphEdge

        wf = Workflow(
            workflow_id="cp-test",
            name="cp",
            start_node="start",
            end_node="end",
            nodes=[
                GraphNode(node_id="start", node_type="start"),
                GraphNode(node_id="end", node_type="end"),
            ],
            edges=[GraphEdge(from_node="start", to_node="end")],
        )

        result = await execute_workflow_checkpointed(
            wf,
            tenant_id="admin",
            inputs={"topic": "x"},
        )
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.execution_id)
        cp = get_graph_checkpoint_store().get(result.execution_id or "")
        self.assertIsNotNone(cp)
        self.assertEqual(cp.status, "completed")


if __name__ == "__main__":
    unittest.main()
