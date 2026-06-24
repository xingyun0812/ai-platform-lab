#!/usr/bin/env python3
"""Phase O #87 — Task Planner 模块 smoke（mock LLM，无 Gateway）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_structured_plan_path() -> None:
    """Smoke test for Q1 structured plan path (Issue #116, mock LLM, no real API)."""
    from packages.agent.planner import (
        build_response_format_schema,
        generate_plan,
        is_structured_mode,
    )

    # 1. Verify helpers
    schema = build_response_format_schema()
    assert schema["type"] == "json_schema"
    js = schema["json_schema"]
    assert js["name"] == "agent_plan"
    assert js["strict"] is True
    inner = js["schema"]
    assert "goal" in inner["properties"]
    assert "steps" in inner["properties"]
    assert inner["additionalProperties"] is False
    print("  [ok] build_response_format_schema keys verified")

    sample = {
        "goal": "structured smoke goal",
        "steps": [
            {
                "id": "s1",
                "description": "检索资料",
                "tool_hint": "get_kb_snippet",
                "depends_on": [],
            },
            {
                "id": "s2",
                "description": "计算",
                "tool_hint": "calc",
                "depends_on": ["s1"],
            },
        ],
    }

    captured_payloads: list[dict] = []

    async def mock_llm(body):
        captured_payloads.append(body)

        class Route:
            status = 200
            body = {"choices": [{"message": {"content": json.dumps(sample, ensure_ascii=False)}}]}
            error = None

        return Route()

    async def _run_structured() -> None:
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
            with patch("packages.agent.planner.forward_with_model_router", mock_llm):
                plan, _ = await generate_plan(
                    goal=sample["goal"],
                    allowed_models=(),
                    allowed_tools=(),
                )
        assert plan.goal == sample["goal"]
        assert len(plan.steps) == 2
        # Verify response_format was injected
        assert len(captured_payloads) == 1, "Should be 1 upstream call on success"
        rf = captured_payloads[0].get("response_format")
        assert rf is not None, "response_format must be present in structured mode"
        assert rf["type"] == "json_schema"
        print("  [ok] structured path: plan parsed, response_format injected")

    asyncio.run(_run_structured())

    # 2. Test fallback path
    captured_payloads.clear()
    call_count = [0]

    async def mock_llm_fallback(body):
        call_count[0] += 1
        captured_payloads.append(body)
        if call_count[0] == 1:

            class BadRoute:
                status = 200
                body = {"choices": [{"message": {"content": "BROKEN JSON {{{"}}]}
                error = None

            return BadRoute()

        class GoodRoute:
            status = 200
            body = {"choices": [{"message": {"content": json.dumps(sample, ensure_ascii=False)}}]}
            error = None

        return GoodRoute()

    async def _run_fallback() -> None:
        with patch.dict(os.environ, {"PLAN_OUTPUT_MODE": "structured"}):
            with patch("packages.agent.planner.forward_with_model_router", mock_llm_fallback):
                plan, _ = await generate_plan(
                    goal="fallback smoke test",
                    allowed_models=(),
                    allowed_tools=(),
                )
        assert plan.goal == sample["goal"]
        assert call_count[0] == 2, "Expected 2 calls: structured attempt + legacy fallback"
        # First call had response_format, second did not
        assert "response_format" in captured_payloads[0]
        assert "response_format" not in captured_payloads[1]
        print("  [ok] fallback path: degraded gracefully, plan parsed from legacy call")

    asyncio.run(_run_fallback())

    print("  [ok] test_structured_plan_path PASSED")


def main() -> int:
    from packages.agent.planner import (
        PlannerError,
        build_planner_user_prompt,
        execute_plan_with_agent,
        extract_json_object,
        generate_plan,
        ordered_plan_steps,
        parse_plan,
        validate_plan,
    )
    from packages.contracts.agent_schemas import AgentPlan, PlanStep

    sample = {
        "goal": "查资料并计算",
        "steps": [
            {
                "id": "s1",
                "description": "检索 RAG 资料",
                "tool_hint": "get_kb_snippet",
                "depends_on": [],
            },
            {
                "id": "s2",
                "description": "计算 1+2",
                "tool_hint": "calc",
                "depends_on": ["s1"],
            },
        ],
    }
    plan = parse_plan(sample)
    assert len(ordered_plan_steps(plan)) == 2

    prompt = build_planner_user_prompt(
        goal="demo",
        context="ctx",
        available_tools=("calc", "get_kb_snippet"),
    )
    assert "demo" in prompt and "calc" in prompt

    fenced = extract_json_object("```json\n" + json.dumps(sample) + "\n```")
    assert fenced["goal"] == sample["goal"]

    try:
        validate_plan(AgentPlan(goal="   ", steps=[PlanStep(id="s1", description="x")]))
        print("ERROR: empty goal should fail", file=sys.stderr)
        return 1
    except PlannerError as e:
        assert e.code == "PLAN_INVALID"

    async def _mock_llm(*args, **kwargs):
        class Route:
            status = 200
            body = {"choices": [{"message": {"content": json.dumps(sample, ensure_ascii=False)}}]}
            error = None

        return Route()

    async def _run() -> None:
        with patch(
            "packages.agent.planner.forward_with_model_router",
            _mock_llm,
        ):
            plan2, _ = await generate_plan(
                goal="查资料并计算",
                allowed_models=(),
                allowed_tools=(),
            )
        assert plan2.goal == sample["goal"]

        calls: list[str] = []

        async def fake_run(**kwargs):
            calls.append(kwargs["new_messages"][-1]["content"])
            return {
                "final_message": "ok",
                "tool_calls": [],
                "steps": 1,
                "model": "chat-fast",
                "status": "completed",
            }

        await execute_plan_with_agent(
            plan=plan,
            tenant_id="admin",
            session_id="planner-smoke",
            allowed_tools=(),
            allowed_models=(),
            model="chat-fast",
            session_store=_FakeSessionStore(),
            run_agent_fn=fake_run,
        )
        assert len(calls) == 2

    asyncio.run(_run())
    print("OK agent_planner_smoke (legacy path)")

    # Q1 structured plan path test (Issue #116)
    print("Testing Q1 structured plan path (Issue #116)...")
    test_structured_plan_path()
    print("OK agent_planner_smoke (structured path)")

    return 0


class _FakeSessionStore:
    def get_session_state(self, tenant_id: str, session_id: str):
        from packages.agent.session_state import SessionState

        return SessionState(messages=[], summary=None, turn_count=0)

    def save_session_state(self, tenant_id: str, session_id: str, state) -> None:
        return None


def test_parallel_execution() -> int:
    """Smoke test for Q2 DAG parallel plan step execution (mock, no real API)."""
    from packages.agent.planner import execute_plan_parallel, plan_execution_layers
    from packages.contracts.agent_schemas import AgentPlan, PlanStep

    # Test plan_execution_layers with a diamond dependency
    s1 = PlanStep(id="s1", description="root", depends_on=[])
    s2 = PlanStep(id="s2", description="branch A", depends_on=["s1"])
    s3 = PlanStep(id="s3", description="branch B", depends_on=["s1"])
    s4 = PlanStep(id="s4", description="merge", depends_on=["s2", "s3"])

    layers = plan_execution_layers([s1, s2, s3, s4])
    assert len(layers) == 3, f"Expected 3 layers, got {len(layers)}"
    assert [s.id for s in layers[0]] == ["s1"]
    layer1_ids = sorted(s.id for s in layers[1])
    assert layer1_ids == ["s2", "s3"], f"Layer 1 ids mismatch: {layer1_ids}"
    assert [s.id for s in layers[2]] == ["s4"]

    # Test no-deps all-parallel plan
    pa = PlanStep(id="pa", description="a", depends_on=[])
    pb = PlanStep(id="pb", description="b", depends_on=[])
    pc = PlanStep(id="pc", description="c", depends_on=[])
    flat_layers = plan_execution_layers([pa, pb, pc])
    assert len(flat_layers) == 1, f"Expected 1 layer, got {len(flat_layers)}"
    assert len(flat_layers[0]) == 3

    # Test execute_plan_parallel with mock runner
    plan = AgentPlan(goal="parallel smoke", steps=[s1, s2, s3, s4])
    calls: list[str] = []

    async def fake_run(**kwargs):
        calls.append(kwargs["session_id"])
        return {
            "final_message": "ok",
            "tool_calls": [],
            "steps": 1,
            "model": "mock-model",
            "status": "completed",
        }

    import asyncio

    result = asyncio.run(
        execute_plan_parallel(
            plan=plan,
            tenant_id="smoke-tenant",
            session_id="smoke-session",
            allowed_tools=(),
            allowed_models=(),
            model="mock-model",
            session_store=None,
            run_agent_fn=fake_run,
        )
    )
    assert result["plan_steps_completed"] == 4, (
        f"Expected 4 steps, got {result['plan_steps_completed']}"
    )
    assert result["status"] == "completed", f"Expected completed, got {result['status']}"

    # Verify sub-session IDs were used
    assert any("__step_s1" in c for c in calls), f"sub-session for s1 not found in {calls}"
    assert any("__step_s4" in c for c in calls), f"sub-session for s4 not found in {calls}"

    # Test pending_approval stops later layers
    plan2 = AgentPlan(goal="approval test", steps=[s1, s2])

    async def approval_run(**kwargs):
        return {
            "final_message": "approval needed",
            "tool_calls": [],
            "steps": 0,
            "model": "mock-model",
            "status": "pending_approval",
            "approval_id": "appr-smoke-001",
        }

    result2 = asyncio.run(
        execute_plan_parallel(
            plan=plan2,
            tenant_id="smoke-tenant",
            session_id="smoke-session-2",
            allowed_tools=(),
            allowed_models=(),
            model="mock-model",
            session_store=None,
            run_agent_fn=approval_run,
        )
    )
    assert result2["status"] == "pending_approval", (
        f"Expected pending_approval, got {result2['status']}"
    )
    assert result2["approval_id"] == "appr-smoke-001"

    print("OK test_parallel_execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
