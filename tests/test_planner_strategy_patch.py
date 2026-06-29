"""tests/test_planner_strategy_patch.py — #146 7c approved patch → generate_plan 注入。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from packages.agent.planner import build_planner_user_prompt, generate_plan
from packages.agent.self_evolve import (
    StrategyPatch,
    approve_strategy_patch,
    format_approved_strategy_context,
    reset_strategy_patch_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan


def _run(coro):
    return asyncio.run(coro)


class TestFormatApprovedStrategyContext(unittest.TestCase):
    def setUp(self) -> None:
        reset_strategy_patch_store_for_tests()

    def tearDown(self) -> None:
        reset_strategy_patch_store_for_tests()

    def test_returns_empty_without_approved(self) -> None:
        self.assertEqual(format_approved_strategy_context("t1"), "")

    def test_includes_approved_patch_fields(self) -> None:
        store = __import__(
            "packages.agent.self_evolve",
            fromlist=["get_strategy_patch_store"],
        ).get_strategy_patch_store()
        patch = StrategyPatch(
            patch_id="p-approved-1",
            tenant_id="t1",
            lessons="L",
            proposed_change={
                "field": "plan_prompt",
                "old": "a",
                "new": "优先拆分子任务",
                "reason": "上次失败",
            },
            status="pending",
        )
        store.add(patch)
        approve_strategy_patch("p-approved-1", decided_by="reviewer")

        ctx = format_approved_strategy_context("t1")
        self.assertIn("plan_prompt", ctx)
        self.assertIn("优先拆分子任务", ctx)
        self.assertIn("上次失败", ctx)


class TestGeneratePlanStrategyInjection(unittest.TestCase):
    def setUp(self) -> None:
        reset_strategy_patch_store_for_tests()
        store = __import__(
            "packages.agent.self_evolve",
            fromlist=["get_strategy_patch_store"],
        ).get_strategy_patch_store()
        store.add(
            StrategyPatch(
                patch_id="p-inject-1",
                tenant_id="admin",
                lessons="L",
                proposed_change={
                    "field": "plan_prompt",
                    "old": "x",
                    "new": "INJECTED_STRATEGY_MARKER",
                    "reason": "test",
                },
                status="pending",
            )
        )
        approve_strategy_patch("p-inject-1")

    def tearDown(self) -> None:
        reset_strategy_patch_store_for_tests()

    def test_generate_plan_includes_approved_strategy_in_prompt(self) -> None:
        captured: dict = {}

        async def fake_route(payload, requested_model=None, tenant_default=None):
            captured["messages"] = payload.get("messages")
            return type(
                "R",
                (),
                {
                    "status": 200,
                    "body": {
                        "choices": [
                            {
                                "message": {
                                    "content": '{"goal":"g","steps":[{"id":"s1","description":"d","depends_on":[]}]}'
                                }
                            }
                        ]
                    },
                    "error": None,
                },
            )()

        with patch("packages.agent.planner.forward_with_model_router", fake_route):
            with patch("packages.agent.planner.is_structured_mode", return_value=False):
                plan, _ = _run(
                    generate_plan(
                        goal="测试目标",
                        allowed_models=("chat-fast",),
                        allowed_tools=(),
                        tenant_id="admin",
                        model="chat-fast",
                    )
                )

        self.assertIsInstance(plan, AgentPlan)
        user_msg = captured["messages"][1]["content"]
        self.assertIn("INJECTED_STRATEGY_MARKER", user_msg)
        self.assertIn("已审批策略", user_msg)

    def test_no_tenant_id_skips_injection(self) -> None:
        prompt = build_planner_user_prompt(goal="g", context=None, available_tools=())
        self.assertNotIn("已审批策略", prompt)


if __name__ == "__main__":
    unittest.main()
