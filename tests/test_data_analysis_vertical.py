"""Phase O #93 — 数据分析 Vertical 单测。"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


class DataAnalysisYamlTests(unittest.TestCase):
    def test_workflow_yaml_parses(self) -> None:
        from packages.agent.orchestrator.graph import parse_workflow

        data = yaml.safe_load(
            (REPO / "config" / "workflows" / "data_analysis.yaml").read_text(encoding="utf-8")
        )
        wf = parse_workflow(data["workflows"][0])
        self.assertEqual(wf.workflow_id, "data-analysis-vertical")
        types = {n.node_type for n in wf.nodes}
        self.assertIn("tool_call", types)
        self.assertIn("output", types)

    def test_workflow_has_three_tool_calls(self) -> None:
        from packages.agent.orchestrator.graph import parse_workflow

        data = yaml.safe_load(
            (REPO / "config" / "workflows" / "data_analysis.yaml").read_text(encoding="utf-8")
        )
        wf = parse_workflow(data["workflows"][0])
        tool_nodes = [n for n in wf.nodes if n.node_type == "tool_call"]
        names = {n.config.get("tool_name") for n in tool_nodes}
        self.assertEqual(names, {"web_search", "sql_query", "calc"})


class WorkflowStoreTests(unittest.TestCase):
    def test_extra_dir_loads_vertical(self) -> None:
        from packages.agent.orchestrator.workflow_store import WorkflowStore

        store = WorkflowStore(
            yaml_path=REPO / "config" / "orchestrator_workflows.yaml",
            extra_workflows_dir=REPO / "config" / "workflows",
        )
        store.load()
        wf = store.get_workflow("data-analysis-vertical")
        self.assertIsNotNone(wf)


class ExecuteWorkflowTests(unittest.TestCase):
    def test_mock_execute_completes(self) -> None:
        from packages.agent.orchestrator.engine import execute_workflow
        from packages.agent.orchestrator.workflow_store import WorkflowStore

        store = WorkflowStore(
            yaml_path=REPO / "config" / "orchestrator_workflows.yaml",
            extra_workflows_dir=REPO / "config" / "workflows",
        )
        store.load()
        wf = store.get_workflow("data-analysis-vertical")
        assert wf is not None

        result = asyncio.run(
            execute_workflow(wf, inputs={"topic": "SaaS analytics"}, timeout_seconds=30.0)
        )
        self.assertEqual(result.status, "completed")
        report = str((result.outputs.get("report") or {}).get("value") or "")
        self.assertIn("数据分析报告", report)
        self.assertIn("demo_sales", str(result.outputs).lower() + report.lower())

    def test_report_contains_calc_result(self) -> None:
        from packages.agent.orchestrator.engine import execute_workflow
        from packages.agent.orchestrator.workflow_store import WorkflowStore

        store = WorkflowStore(
            extra_workflows_dir=REPO / "config" / "workflows",
        )
        store.load()
        wf = store.get_workflow("data-analysis-vertical")
        assert wf is not None
        result = asyncio.run(execute_workflow(wf, inputs={"topic": "test"}))
        self.assertIn("calc_yoy", result.outputs)


class AgentSpecTests(unittest.TestCase):
    def test_data_analyst_tools(self) -> None:
        from packages.agent.multi_agent.registry import AgentRegistry

        reg = AgentRegistry(yaml_path=REPO / "config" / "agents.yaml")
        reg.load()
        spec = reg.get_agent("data_analyst")
        self.assertIsNotNone(spec)
        self.assertTrue(spec.is_tool_allowed("web_search"))
        self.assertTrue(spec.is_tool_allowed("sql_query"))
        self.assertTrue(spec.is_tool_allowed("calc"))
        self.assertFalse(spec.is_tool_allowed("httpbin_delay"))


class EvalSmokeTests(unittest.TestCase):
    def test_run_mock_checks(self) -> None:
        from eval.data_analysis_vertical import run_mock_checks

        checks = asyncio.run(run_mock_checks())
        failed = [c for c in checks if not c.passed]
        self.assertEqual(failed, [], [f"{c.name}: {c.detail}" for c in failed])


if __name__ == "__main__":
    unittest.main()
