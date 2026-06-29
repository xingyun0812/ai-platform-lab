#!/usr/bin/env python3
"""tests/test_plan_workflow.py — Phase Q Q5 Plan to workflow bridge tests.

All tests are standalone (no external dependencies).
Runs under Python 3.11+ (uses asyncio.run for async helpers).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure repo root is on sys.path so bare imports work
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.plan_workflow import (  # noqa: E402
    plan_to_workflow,
    plan_to_workflow_yaml,
    workflow_to_yaml,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    sid: str,
    description: str = "",
    depends_on: list[str] | None = None,
    tool_hint: str | None = None,
    agent_hint: str | None = None,
) -> PlanStep:
    return PlanStep(
        id=sid,
        description=description or f"step {sid}",
        depends_on=depends_on or [],
        tool_hint=tool_hint,
        agent_hint=agent_hint,
    )


def _plan(goal: str, *steps: PlanStep) -> AgentPlan:
    return AgentPlan(goal=goal, steps=list(steps))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlanToWorkflowRequiredKeys(unittest.TestCase):
    """plan_to_workflow output contains all required top-level keys."""

    def test_plan_to_workflow_has_required_keys(self) -> None:
        plan = _plan("Analyze sales", _step("s1"))
        wf = plan_to_workflow(plan)
        for key in ("name", "description", "nodes", "edges", "metadata"):
            self.assertIn(key, wf, f"Missing key: {key}")


class TestPlanToWorkflowNodesCount(unittest.TestCase):
    """nodes list length equals number of plan steps."""

    def test_plan_to_workflow_nodes_count(self) -> None:
        plan = _plan("Goal", _step("s1"), _step("s2"), _step("s3"))
        wf = plan_to_workflow(plan)
        self.assertEqual(len(wf["nodes"]), 3)


class TestPlanToWorkflowLinearEdges(unittest.TestCase):
    """s1→s2 dependency produces correct edge entry."""

    def test_plan_to_workflow_linear_edges(self) -> None:
        plan = _plan(
            "Goal",
            _step("s1"),
            _step("s2", depends_on=["s1"]),
        )
        wf = plan_to_workflow(plan)
        self.assertEqual(len(wf["edges"]), 1)
        edge = wf["edges"][0]
        self.assertEqual(edge["from"], "s1")
        self.assertEqual(edge["to"], "s2")


class TestPlanToWorkflowNoDepsNoEdges(unittest.TestCase):
    """Plan with no depends_on produces empty edges list."""

    def test_plan_to_workflow_no_deps_no_edges(self) -> None:
        plan = _plan("Goal", _step("s1"), _step("s2"), _step("s3"))
        wf = plan_to_workflow(plan)
        self.assertEqual(wf["edges"], [])


class TestPlanToWorkflowNameFromGoal(unittest.TestCase):
    """name field is derived from goal (max 50 chars)."""

    def test_plan_to_workflow_name_from_goal(self) -> None:
        short_goal = "Short goal"
        plan = _plan(short_goal, _step("s1"))
        wf = plan_to_workflow(plan)
        self.assertEqual(wf["name"], short_goal)

    def test_plan_to_workflow_name_truncated_at_50(self) -> None:
        long_goal = "A" * 100
        plan = _plan(long_goal, _step("s1"))
        wf = plan_to_workflow(plan)
        self.assertEqual(len(wf["name"]), 50)
        self.assertEqual(wf["name"], long_goal[:50])

    def test_plan_to_workflow_description_is_full_goal(self) -> None:
        long_goal = "B" * 100
        plan = _plan(long_goal, _step("s1"))
        wf = plan_to_workflow(plan)
        self.assertEqual(wf["description"], long_goal)


class TestPlanToWorkflowNodeConfigFields(unittest.TestCase):
    """Each node.config contains description, tool_hint, and agent_hint."""

    def test_plan_to_workflow_node_config_fields(self) -> None:
        plan = _plan(
            "Goal",
            _step("s1", description="Do something", tool_hint="calc", agent_hint="agent-1"),
        )
        wf = plan_to_workflow(plan)
        node = wf["nodes"][0]
        self.assertEqual(node["id"], "s1")
        self.assertEqual(node["type"], "agent")
        config = node["config"]
        self.assertIn("description", config)
        self.assertIn("tool_hint", config)
        self.assertIn("agent_hint", config)
        self.assertEqual(config["description"], "Do something")
        self.assertEqual(config["tool_hint"], "calc")
        self.assertEqual(config["agent_hint"], "agent-1")

    def test_plan_to_workflow_node_config_null_hints(self) -> None:
        plan = _plan("Goal", _step("s1"))
        wf = plan_to_workflow(plan)
        config = wf["nodes"][0]["config"]
        self.assertIsNone(config["tool_hint"])
        self.assertIsNone(config["agent_hint"])


class TestWorkflowToYamlIsValidYaml(unittest.TestCase):
    """workflow_to_yaml output can be parsed back by yaml.safe_load."""

    def test_workflow_to_yaml_is_valid_yaml(self) -> None:
        import yaml

        plan = _plan("Goal", _step("s1"), _step("s2", depends_on=["s1"]))
        wf = plan_to_workflow(plan)
        yaml_str = workflow_to_yaml(wf)
        parsed = yaml.safe_load(yaml_str)
        self.assertIsInstance(parsed, dict)
        self.assertIn("nodes", parsed)


class TestPlanToWorkflowYamlRoundtrip(unittest.TestCase):
    """YAML output round-trips back to a dict with expected nodes."""

    def test_plan_to_workflow_yaml_roundtrip(self) -> None:
        import yaml

        plan = _plan(
            "Analyze Q2 sales data",
            _step("s1", description="Fetch data"),
            _step("s2", description="Analyze data", depends_on=["s1"]),
        )
        yaml_str = plan_to_workflow_yaml(plan)
        parsed = yaml.safe_load(yaml_str)
        self.assertIsInstance(parsed, dict)
        self.assertIn("nodes", parsed)
        self.assertEqual(len(parsed["nodes"]), 2)
        self.assertEqual(parsed["nodes"][0]["id"], "s1")
        self.assertEqual(parsed["nodes"][1]["id"], "s2")


class TestPlanToWorkflowMetadata(unittest.TestCase):
    """metadata contains generated_by, plan_steps, and source."""

    def test_plan_to_workflow_metadata(self) -> None:
        plan = _plan("Goal", _step("s1"), _step("s2"))
        wf = plan_to_workflow(plan)
        meta = wf["metadata"]
        self.assertEqual(meta["generated_by"], "plan_to_workflow")
        self.assertEqual(meta["plan_steps"], 2)
        self.assertEqual(meta["source"], "AgentPlan")


class TestPlanToWorkflowMultipleEdges(unittest.TestCase):
    """Diamond-pattern plan produces correct number of edges."""

    def test_plan_to_workflow_diamond_edges(self) -> None:
        # s1 → s2, s3 → s4
        plan = _plan(
            "Diamond goal",
            _step("s1"),
            _step("s2", depends_on=["s1"]),
            _step("s3", depends_on=["s1"]),
            _step("s4", depends_on=["s2", "s3"]),
        )
        wf = plan_to_workflow(plan)
        # Expected edges: s1→s2, s1→s3, s2→s4, s3→s4
        self.assertEqual(len(wf["edges"]), 4)
        edge_pairs = {(e["from"], e["to"]) for e in wf["edges"]}
        self.assertIn(("s1", "s2"), edge_pairs)
        self.assertIn(("s1", "s3"), edge_pairs)
        self.assertIn(("s2", "s4"), edge_pairs)
        self.assertIn(("s3", "s4"), edge_pairs)


class TestPlanToWorkflowNodeOrder(unittest.TestCase):
    """nodes appear in the same order as plan.steps."""

    def test_plan_to_workflow_node_order(self) -> None:
        plan = _plan("Goal", _step("s1"), _step("s2"), _step("s3"))
        wf = plan_to_workflow(plan)
        ids = [n["id"] for n in wf["nodes"]]
        self.assertEqual(ids, ["s1", "s2", "s3"])


# ---------------------------------------------------------------------------
# Route smoke test (no network, uses TestClient)
# ---------------------------------------------------------------------------


class TestPlanExportRoute(unittest.TestCase):
    """POST /v1/agent/plan/export returns text/yaml (uses TestClient)."""

    def _build_app(self):
        """Create a minimal FastAPI app with only the plan_workflow router."""
        from fastapi import FastAPI

        from apps.gateway.agent.plan_workflow_routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    def _fake_tenants(self):
        from apps.gateway.tenants import TenantRecord

        return {
            "t1": TenantRecord(
                tenant_id="t1",
                bearer_token="tok1",
                daily_request_quota=-1,
                allowed_models=(),
                allowed_tools=(),
                default_model=None,
                rate_limit_rps=100.0,
                rate_limit_burst=200,
                token_budget_daily=-1,
                token_budget_monthly=-1,
                role="user",
            )
        }

    def test_plan_export_route_returns_yaml(self) -> None:
        """POST /v1/agent/plan/export with valid plan returns 200 text/yaml."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        app = self._build_app()
        tenants = self._fake_tenants()

        payload = {
            "plan": {
                "goal": "Test goal",
                "steps": [
                    {"id": "s1", "description": "Step 1", "depends_on": []},
                    {"id": "s2", "description": "Step 2", "depends_on": ["s1"]},
                ],
            }
        }

        with patch("apps.gateway.agent.plan_workflow_routes.load_tenants", return_value=tenants):
            with patch("packages.platform.get_settings") as mock_settings:
                mock_settings.return_value.auth_jwt_enabled = False
                with TestClient(app) as client:
                    resp = client.post(
                        "/v1/agent/plan/export",
                        json=payload,
                        headers={
                            "X-Tenant-Id": "t1",
                            "Authorization": "Bearer tok1",
                        },
                    )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/yaml", resp.headers.get("content-type", ""))

    def test_plan_export_route_missing_plan_returns_422(self) -> None:
        """POST /v1/agent/plan/export without plan field returns 422."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        app = self._build_app()
        tenants = self._fake_tenants()

        with patch("apps.gateway.agent.plan_workflow_routes.load_tenants", return_value=tenants):
            with patch("packages.platform.get_settings") as mock_settings:
                mock_settings.return_value.auth_jwt_enabled = False
                with TestClient(app) as client:
                    resp = client.post(
                        "/v1/agent/plan/export",
                        json={"other": "data"},
                        headers={
                            "X-Tenant-Id": "t1",
                            "Authorization": "Bearer tok1",
                        },
                    )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
