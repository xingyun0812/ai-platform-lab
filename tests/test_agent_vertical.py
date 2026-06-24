"""Phase L #59 — Agent Vertical 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_agents_yaml_has_rag_specialist():
    data = yaml.safe_load((REPO / "config" / "agents.yaml").read_text(encoding="utf-8"))
    ids = [a["agent_id"] for a in data.get("agents", []) if isinstance(a, dict)]
    assert "rag_specialist" in ids


def test_orchestrator_workflow_yaml_parses():
    from packages.agent.orchestrator.graph import parse_workflow

    data = yaml.safe_load((REPO / "config" / "orchestrator_workflows.yaml").read_text(encoding="utf-8"))
    wf = parse_workflow(data["workflows"][0])
    assert wf.workflow_id == "agent-vertical-rag"
    assert any(n.node_type == "agent_call" for n in wf.nodes)


def test_agent_registry_loads_rag_specialist():
    from packages.agent.multi_agent.registry import AgentRegistry

    reg = AgentRegistry(yaml_path=REPO / "config" / "agents.yaml")
    reg.load()
    spec = reg.get_agent("rag_specialist")
    assert spec is not None
    assert "get_kb_snippet" in spec.allowed_tools


def test_workflow_store_loads_vertical():
    from packages.agent.orchestrator.workflow_store import WorkflowStore

    store = WorkflowStore(yaml_path=REPO / "config" / "orchestrator_workflows.yaml")
    store.load()
    wf = store.get_workflow("agent-vertical-rag")
    assert wf is not None


def test_hitl_pending_confirm_cycle():
    from packages.agent.hitl import confirm_execution, create_pending_execution

    pending = create_pending_execution(
        tenant_id="admin",
        session_id="test-vertical",
        tool_name="httpbin_delay",
        arguments={"seconds": 1},
    )
    confirmed = confirm_execution(approval_id=pending.approval_id, reviewer="admin")
    assert confirmed.status.value in ("confirmed", "approved")


def test_httpbin_requires_hitl():
    from packages.agent.risk import tool_requires_hitl

    assert tool_requires_hitl("httpbin_delay") is True
    assert tool_requires_hitl("calc") is False


def test_audit_log_pending_includes_approval_id():
    import asyncio

    async def _run():
        from packages.agent.runner import _audit_tool_action
        from packages.audit.action_levels import init_classifier
        from packages.audit.action_levels import reset_for_tests as reset_cls
        from packages.audit.action_logger import (
            get_action_logger,
            init_action_logger,
            reset_for_tests,
        )

        reset_for_tests()
        reset_cls()
        init_classifier(yaml_path=REPO / "config" / "tool_classifications.yaml")
        init_action_logger()
        await _audit_tool_action(
            tenant_id="admin",
            session_id="s1",
            tool_name="httpbin_delay",
            arguments={"seconds": 1},
            status="pending",
            approval_id="appr-test-001",
        )
        logger = get_action_logger()
        assert logger is not None
        entries = await logger.list_actions("admin", action_level="network")
        assert any(e.approval_id == "appr-test-001" for e in entries)
        reset_for_tests()
        reset_cls()

    asyncio.run(_run())


def test_audit_log_success_after_tool():
    import asyncio

    async def _run():
        from packages.agent.runner import _audit_tool_action
        from packages.audit.action_levels import init_classifier
        from packages.audit.action_levels import reset_for_tests as reset_cls
        from packages.audit.action_logger import (
            get_action_logger,
            init_action_logger,
            reset_for_tests,
        )

        reset_for_tests()
        reset_cls()
        init_classifier(yaml_path=REPO / "config" / "tool_classifications.yaml")
        init_action_logger()
        await _audit_tool_action(
            tenant_id="admin",
            session_id="s2",
            tool_name="calc",
            arguments={"expression": "1+1"},
            status="success",
            result_summary="2",
        )
        logger = get_action_logger()
        assert logger is not None
        entries = await logger.list_actions("admin")
        assert any(e.tool_name == "calc" and e.status == "success" for e in entries)
        reset_for_tests()
        reset_cls()

    asyncio.run(_run())


def test_vertical_hitl_01_in_agent_scenarios():
    path = REPO / "eval" / "baselines" / "agent_scenarios.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hitl = next((r for r in rows if r.get("id") == "vertical-hitl-01"), None)
    assert hitl is not None
    assert hitl.get("require_tools") is True
    assert "httpbin_delay" in hitl.get("expect_tools", [])


def test_agent_vertical_smoke_constants():
    from eval.agent_vertical_smoke import RAG_AGENT_ID, VERTICAL_WORKFLOW_ID

    assert RAG_AGENT_ID == "rag_specialist"
    assert VERTICAL_WORKFLOW_ID == "agent-vertical-rag"
