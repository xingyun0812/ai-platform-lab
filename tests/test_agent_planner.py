#!/usr/bin/env python3
"""tests/test_agent_planner.py — Phase O #87 Task Planner。"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.planner import (  # noqa: E402
    PlannerError,
    build_planner_user_prompt,
    execute_plan_with_agent,
    extract_json_object,
    generate_plan,
    ordered_plan_steps,
    parse_plan,
    topological_sort,
    validate_plan,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _plan_payload(**overrides) -> dict:
    base = {
        "goal": "测试目标",
        "steps": [
            {"id": "s1", "description": "第一步", "depends_on": []},
        ],
    }
    base.update(overrides)
    return base


class TestParsePlan(unittest.TestCase):
    def test_single_step(self) -> None:
        plan = parse_plan(_plan_payload())
        self.assertEqual(len(plan.steps), 1)

    def test_multi_step_deps(self) -> None:
        data = _plan_payload(
            steps=[
                {"id": "s1", "description": "a", "depends_on": []},
                {"id": "s2", "description": "b", "depends_on": ["s1"]},
            ]
        )
        order = ordered_plan_steps(parse_plan(data))
        self.assertEqual([s.id for s in order], ["s1", "s2"])


class TestValidatePlan(unittest.TestCase):
    def test_empty_goal(self) -> None:
        with self.assertRaises(PlannerError) as ctx:
            validate_plan(AgentPlan(goal="  ", steps=[PlanStep(id="s1", description="x")]))
        self.assertEqual(ctx.exception.code, "PLAN_INVALID")

    def test_empty_steps(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            AgentPlan(goal="g", steps=[])

    def test_duplicate_ids(self) -> None:
        with self.assertRaises(PlannerError) as ctx:
            parse_plan(
                {
                    "goal": "g",
                    "steps": [
                        {"id": "s1", "description": "a", "depends_on": []},
                        {"id": "s1", "description": "b", "depends_on": []},
                    ],
                }
            )
        self.assertEqual(ctx.exception.code, "PLAN_INVALID")

    def test_missing_dependency(self) -> None:
        with self.assertRaises(PlannerError) as ctx:
            parse_plan(
                {
                    "goal": "g",
                    "steps": [{"id": "s1", "description": "a", "depends_on": ["missing"]}],
                }
            )
        self.assertEqual(ctx.exception.code, "PLAN_INVALID")

    def test_cycle_rejected(self) -> None:
        steps = [
            PlanStep(id="s1", description="a", depends_on=["s2"]),
            PlanStep(id="s2", description="b", depends_on=["s1"]),
        ]
        self.assertIsNone(topological_sort(steps))
        with self.assertRaises(PlannerError) as ctx:
            validate_plan(AgentPlan(goal="g", steps=steps))
        self.assertEqual(ctx.exception.code, "PLAN_CYCLE")


class TestExtractJson(unittest.TestCase):
    def test_plain_json(self) -> None:
        data = extract_json_object('{"goal":"g","steps":[{"id":"s1","description":"d","depends_on":[]}]}')
        self.assertEqual(data["goal"], "g")

    def test_fenced_json(self) -> None:
        inner = {"goal": "g", "steps": [{"id": "s1", "description": "d", "depends_on": []}]}
        data = extract_json_object("```json\n" + json.dumps(inner) + "\n```")
        self.assertEqual(data["goal"], "g")

    def test_empty_raises(self) -> None:
        with self.assertRaises(PlannerError):
            extract_json_object("")


class TestPlannerPrompt(unittest.TestCase):
    def test_fallback_prompt_contains_goal(self) -> None:
        text = build_planner_user_prompt(
            goal="分析销售",
            context="月报摘要",
            available_tools=("calc",),
        )
        self.assertIn("分析销售", text)
        self.assertIn("calc", text)


class TestGeneratePlan(unittest.TestCase):
    def test_generate_plan_mock_llm(self) -> None:
        payload = _plan_payload(
            steps=[
                {"id": "s1", "description": "检索", "tool_hint": "get_kb_snippet", "depends_on": []},
                {"id": "s2", "description": "计算", "tool_hint": "calc", "depends_on": ["s1"]},
            ]
        )

        async def fake_route(body):
            class R:
                status = 200
                body = {"choices": [{"message": {"content": json.dumps(payload)}}]}
                error = None

            return R()

        async def _run():
            with patch("packages.agent.planner.forward_with_model_router", fake_route):
                plan, model = await generate_plan(
                    goal="查 RAG 并 calc",
                    allowed_models=(),
                    allowed_tools=("calc", "get_kb_snippet"),
                )
            self.assertEqual(len(plan.steps), 2)
            self.assertTrue(model)

        asyncio.run(_run())


class TestExecutePlan(unittest.TestCase):
    def test_execute_calls_runner_per_step(self) -> None:
        plan = parse_plan(
            {
                "goal": "g",
                "steps": [
                    {"id": "s1", "description": "one", "depends_on": []},
                    {"id": "s2", "description": "two", "depends_on": ["s1"]},
                ],
            }
        )
        seen: list[str] = []

        async def fake_run(**kwargs):
            seen.append(kwargs["new_messages"][-1]["content"])
            return {
                "final_message": "step ok",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        class MemStore:
            def get_session_state(self, tenant_id, session_id):
                from packages.agent.session_state import SessionState

                return SessionState(messages=[], summary=None, turn_count=0)

            def save_session_state(self, tenant_id, session_id, state):
                return None

        async def _run():
            result = await execute_plan_with_agent(
                plan=plan,
                tenant_id="t",
                session_id="s",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=MemStore(),
                run_agent_fn=fake_run,
            )
            self.assertEqual(result["plan_steps_completed"], 2)
            self.assertEqual(len(seen), 2)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
