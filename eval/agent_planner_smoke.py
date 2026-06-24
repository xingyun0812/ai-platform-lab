#!/usr/bin/env python3
"""Phase O #87 — Task Planner 模块 smoke（mock LLM，无 Gateway）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


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
            body = {
                "choices": [{"message": {"content": json.dumps(sample, ensure_ascii=False)}}]
            }
            error = None

        return Route()

    async def _run() -> None:
        from unittest.mock import patch

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
    print("OK agent_planner_smoke")
    return 0


class _FakeSessionStore:
    def get_session_state(self, tenant_id: str, session_id: str):
        from packages.agent.session_state import SessionState

        return SessionState(messages=[], summary=None, turn_count=0)

    def save_session_state(self, tenant_id: str, session_id: str, state) -> None:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
