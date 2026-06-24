#!/usr/bin/env python3
"""tests/test_plan_critic.py — Phase Q #118 Replan on failure Critic tests."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.plan_critic import (  # noqa: E402
    _call_upstream,
    _extract_json_from_text,
    build_critic_prompt,
    replan_after_failure,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(goal: str = "test goal", steps: list[dict] | None = None) -> AgentPlan:
    steps = steps or [
        {"id": "s1", "description": "第一步", "depends_on": []},
        {"id": "s2", "description": "第二步", "depends_on": ["s1"]},
    ]
    return AgentPlan(
        goal=goal,
        steps=[PlanStep(**s) for s in steps],
    )


def _make_step(sid: str = "s1", desc: str = "做某事") -> PlanStep:
    return PlanStep(id=sid, description=desc, depends_on=[])


def _plan_json_str(plan: AgentPlan) -> str:
    """Build JSON string for the given plan."""
    plan_dict = {
        "goal": plan.goal,
        "steps": [
            {
                "id": s.id,
                "description": s.description,
                "tool_hint": s.tool_hint,
                "agent_hint": s.agent_hint,
                "depends_on": s.depends_on,
            }
            for s in plan.steps
        ],
    }
    return json.dumps(plan_dict, ensure_ascii=False)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# build_critic_prompt tests
# ---------------------------------------------------------------------------


class TestBuildCriticPrompt(unittest.TestCase):
    def test_build_critic_prompt_contains_plan(self) -> None:
        """prompt 应包含 plan goal 和 failed step id。"""
        plan = _make_plan(goal="分析销售数据")
        step = _make_step("s1", "第一步操作")
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason="工具调用超时",
        )
        self.assertIn("分析销售数据", prompt)
        self.assertIn("s1", prompt)

    def test_build_critic_prompt_contains_failure_reason(self) -> None:
        """prompt 应包含 failure_reason。"""
        plan = _make_plan()
        step = _make_step("s2")
        failure_reason = "API rate limit exceeded"
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason=failure_reason,
        )
        self.assertIn(failure_reason, prompt)

    def test_build_critic_prompt_with_context(self) -> None:
        """提供 context 时 prompt 应包含背景信息。"""
        plan = _make_plan()
        step = _make_step("s1")
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason="失败了",
            context="这是重要的背景信息",
        )
        self.assertIn("这是重要的背景信息", prompt)

    def test_build_critic_prompt_without_context(self) -> None:
        """不提供 context 时 prompt 不应有 context_block 段落（背景信息）。"""
        plan = _make_plan()
        step = _make_step("s1")
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason="失败了",
            context=None,
        )
        self.assertNotIn("背景信息", prompt)

    def test_build_critic_prompt_contains_step_description(self) -> None:
        """prompt 应包含 failed step 的 description。"""
        plan = _make_plan()
        step = _make_step("s1", "执行特殊数据库查询")
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason="数据库连接失败",
        )
        self.assertIn("执行特殊数据库查询", prompt)

    def test_build_critic_prompt_plan_json_in_output(self) -> None:
        """prompt 应包含合法 JSON（goal 字段在 JSON 中）。"""
        plan = _make_plan(goal="独特目标XYZ")
        step = _make_step("s1")
        prompt = build_critic_prompt(
            plan=plan,
            failed_step=step,
            failure_reason="timeout",
        )
        self.assertIn('"goal"', prompt)
        self.assertIn("独特目标XYZ", prompt)


# ---------------------------------------------------------------------------
# _extract_json_from_text tests
# ---------------------------------------------------------------------------


class TestExtractJsonFromText(unittest.TestCase):
    def test_plain_json(self) -> None:
        data = _extract_json_from_text('{"goal":"g","steps":[]}')
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["goal"], "g")

    def test_fenced_json(self) -> None:
        inner = {"goal": "g2", "steps": []}
        text = "```json\n" + json.dumps(inner) + "\n```"
        data = _extract_json_from_text(text)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["goal"], "g2")

    def test_invalid_json_returns_none(self) -> None:
        self.assertIsNone(_extract_json_from_text("not json"))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_extract_json_from_text(""))


# ---------------------------------------------------------------------------
# replan_after_failure tests
# ---------------------------------------------------------------------------


class TestReplanAfterFailure(unittest.TestCase):
    def test_replan_returns_none_when_max_attempts_reached(self) -> None:
        """attempt >= max_replan_attempts → 直接返回 None，不调用 LLM。"""
        plan = _make_plan()
        step = _make_step("s1")

        result = _run(
            replan_after_failure(
                plan=plan,
                failed_step=step,
                failure_reason="failed",
                allowed_models=("gpt-4",),
                max_replan_attempts=2,
                attempt=2,  # at limit
            )
        )
        self.assertIsNone(result)

    def test_replan_returns_none_when_attempt_exceeds_max(self) -> None:
        """attempt > max_replan_attempts → 返回 None。"""
        plan = _make_plan()
        step = _make_step("s1")

        result = _run(
            replan_after_failure(
                plan=plan,
                failed_step=step,
                failure_reason="failed",
                allowed_models=("gpt-4",),
                max_replan_attempts=1,
                attempt=5,
            )
        )
        self.assertIsNone(result)

    def test_replan_success_returns_agent_plan(self) -> None:
        """mock _call_upstream 返回合法修订 plan JSON → 解析成功返回 AgentPlan。"""
        original_plan = _make_plan(
            goal="原始目标",
            steps=[
                {"id": "s1", "description": "步骤一", "depends_on": []},
                {"id": "s2", "description": "步骤二", "depends_on": ["s1"]},
            ],
        )
        revised_plan = _make_plan(
            goal="原始目标",
            steps=[
                {"id": "s1", "description": "修订后的步骤一", "depends_on": []},
                {"id": "s2", "description": "步骤二", "depends_on": ["s1"]},
            ],
        )
        revised_json = _plan_json_str(revised_plan)

        step = _make_step("s1", "步骤一")

        def fake_check_model_allowed(model, allowed_models):
            return True, model

        async def fake_call_upstream(model, prompt):
            return revised_json

        with (
            patch(
                "packages.agent.plan_critic._check_model_allowed",
                fake_check_model_allowed,
            ),
            patch(
                "packages.agent.plan_critic._call_upstream",
                fake_call_upstream,
            ),
        ):
            result = _run(
                replan_after_failure(
                    plan=original_plan,
                    failed_step=step,
                    failure_reason="s1 failed",
                    allowed_models=("gpt-4",),
                    max_replan_attempts=2,
                    attempt=0,
                )
            )

        self.assertIsNotNone(result)
        self.assertIsInstance(result, AgentPlan)
        self.assertEqual(result.goal, "原始目标")

    def test_replan_critic_parse_failure_returns_none(self) -> None:
        """mock _call_upstream 返回非法 JSON → 降级返回 None。"""

        def fake_check_model_allowed(model, allowed_models):
            return True, model

        async def fake_call_upstream(model, prompt):
            return "这不是JSON ```{ broken"

        plan = _make_plan()
        step = _make_step("s1")

        with (
            patch(
                "packages.agent.plan_critic._check_model_allowed",
                fake_check_model_allowed,
            ),
            patch(
                "packages.agent.plan_critic._call_upstream",
                fake_call_upstream,
            ),
        ):
            result = _run(
                replan_after_failure(
                    plan=plan,
                    failed_step=step,
                    failure_reason="failed",
                    allowed_models=("gpt-4",),
                    max_replan_attempts=2,
                    attempt=0,
                )
            )

        self.assertIsNone(result)

    def test_replan_critic_upstream_error_returns_none(self) -> None:
        """mock _call_upstream 返回 None（upstream 错误）→ 降级返回 None。"""

        def fake_check_model_allowed(model, allowed_models):
            return True, model

        async def fake_call_upstream(model, prompt):
            return None  # upstream failure

        plan = _make_plan()
        step = _make_step("s1")

        with (
            patch(
                "packages.agent.plan_critic._check_model_allowed",
                fake_check_model_allowed,
            ),
            patch(
                "packages.agent.plan_critic._call_upstream",
                fake_call_upstream,
            ),
        ):
            result = _run(
                replan_after_failure(
                    plan=plan,
                    failed_step=step,
                    failure_reason="failed",
                    allowed_models=("gpt-4",),
                    max_replan_attempts=2,
                    attempt=0,
                )
            )

        self.assertIsNone(result)

    def test_replan_model_not_allowed_returns_none(self) -> None:
        """model 不在白名单 → 返回 None（不调用 upstream）。"""

        def fake_check_model_allowed(model, allowed_models):
            return False, model  # not allowed

        plan = _make_plan()
        step = _make_step("s1")

        with patch(
            "packages.agent.plan_critic._check_model_allowed",
            fake_check_model_allowed,
        ):
            result = _run(
                replan_after_failure(
                    plan=plan,
                    failed_step=step,
                    failure_reason="failed",
                    allowed_models=(),
                    max_replan_attempts=2,
                    attempt=0,
                )
            )

        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# execute_plan_with_agent integration tests
# ---------------------------------------------------------------------------


class TestExecutePlanWithAgentReplan(unittest.TestCase):
    def _make_session_store(self) -> object:
        class MemStore:
            def get_session_state(self, tenant_id, session_id):
                from packages.agent.session_state import SessionState

                return SessionState(messages=[], summary=None, turn_count=0)

            def save_session_state(self, tenant_id, session_id, state):
                return None

        return MemStore()

    def test_execute_plan_with_agent_triggers_replan_on_failure(self) -> None:
        """mock runner 第1次返回 failed，replan 后第2次 completed → plan_revisions 有记录。"""
        from packages.agent.planner import execute_plan_with_agent

        plan = _make_plan(
            goal="执行目标",
            steps=[{"id": "s1", "description": "关键步骤", "depends_on": []}],
        )
        revised_plan = _make_plan(
            goal="执行目标",
            steps=[{"id": "s1", "description": "修订后的关键步骤", "depends_on": []}],
        )

        call_count = 0

        async def fake_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "final_message": "s1 failed due to timeout",
                    "tool_calls": [],
                    "steps": 1,
                    "model": "gpt-4",
                    "status": "failed",
                }
            return {
                "final_message": "s1 completed after replan",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4",
                "status": "completed",
            }

        async def fake_replan(**kwargs):
            return revised_plan

        async def _run_test():
            with patch(
                "packages.agent.plan_critic.replan_after_failure",
                fake_replan,
            ):
                # Patch the name inside planner's local import
                import packages.agent.plan_critic as pc_mod

                pc_mod.replan_after_failure = fake_replan
                try:
                    result = await execute_plan_with_agent(
                        plan=plan,
                        tenant_id="t1",
                        session_id="s1",
                        allowed_tools=(),
                        allowed_models=(),
                        model=None,
                        session_store=self._make_session_store(),
                        run_agent_fn=fake_run,
                        max_replan_attempts=2,
                    )
                finally:
                    # Restore
                    from packages.agent.plan_critic import replan_after_failure as orig

                    pc_mod.replan_after_failure = orig
            return result

        result = _run(_run_test())
        self.assertIn("plan_revisions", result)
        self.assertGreater(len(result["plan_revisions"]), 0)
        self.assertEqual(result["plan_revisions"][0]["failed_step_id"], "s1")

    def test_execute_plan_with_agent_no_replan_on_max_attempts(self) -> None:
        """达到 max_replan_attempts=0 → 不触发 replan，直接返回 failed。"""
        from packages.agent.planner import execute_plan_with_agent

        plan = _make_plan(
            goal="目标",
            steps=[{"id": "s1", "description": "会失败的步骤", "depends_on": []}],
        )

        async def fake_run(**kwargs):
            return {
                "final_message": "step failed",
                "tool_calls": [],
                "steps": 1,
                "model": "gpt-4",
                "status": "failed",
            }

        result = _run(
            execute_plan_with_agent(
                plan=plan,
                tenant_id="t1",
                session_id="s1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=self._make_session_store(),
                run_agent_fn=fake_run,
                max_replan_attempts=0,
            )
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["plan_revisions"], [])

    def test_plan_revisions_format(self) -> None:
        """plan_revisions 每条应包含 attempt、failed_step_id、new_plan_steps_count。"""
        from packages.agent.planner import execute_plan_with_agent

        plan = _make_plan(
            goal="格式测试",
            steps=[{"id": "s1", "description": "测试步骤", "depends_on": []}],
        )
        revised_plan = _make_plan(
            goal="格式测试",
            steps=[
                {"id": "s1", "description": "修订步骤A", "depends_on": []},
                {"id": "s2", "description": "修订步骤B", "depends_on": ["s1"]},
            ],
        )

        async def fake_run(**kwargs):
            return {
                "final_message": "failed",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "failed",
            }

        async def fake_replan(**kwargs):
            return revised_plan

        async def _run_test():
            import packages.agent.plan_critic as pc_mod

            original_fn = pc_mod.replan_after_failure
            pc_mod.replan_after_failure = fake_replan
            try:
                result = await execute_plan_with_agent(
                    plan=plan,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=(),
                    allowed_models=(),
                    model=None,
                    session_store=self._make_session_store(),
                    run_agent_fn=fake_run,
                    max_replan_attempts=1,
                )
            finally:
                pc_mod.replan_after_failure = original_fn
            return result

        result = _run(_run_test())
        revisions = result["plan_revisions"]
        self.assertEqual(len(revisions), 1)
        rev = revisions[0]
        self.assertIn("attempt", rev)
        self.assertIn("failed_step_id", rev)
        self.assertIn("new_plan_steps_count", rev)
        self.assertEqual(rev["attempt"], 1)
        self.assertEqual(rev["failed_step_id"], "s1")
        self.assertEqual(rev["new_plan_steps_count"], 2)

    def test_execute_plan_completed_has_empty_plan_revisions(self) -> None:
        """成功完成的 plan 应返回空 plan_revisions 列表。"""
        from packages.agent.planner import execute_plan_with_agent

        plan = _make_plan(
            goal="成功目标",
            steps=[{"id": "s1", "description": "成功步骤", "depends_on": []}],
        )

        async def fake_run(**kwargs):
            return {
                "final_message": "done",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "completed",
            }

        result = _run(
            execute_plan_with_agent(
                plan=plan,
                tenant_id="t1",
                session_id="s1",
                allowed_tools=(),
                allowed_models=(),
                model=None,
                session_store=self._make_session_store(),
                run_agent_fn=fake_run,
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertIn("plan_revisions", result)
        self.assertEqual(result["plan_revisions"], [])

    def test_execute_plan_critic_returns_none_terminates_failed(self) -> None:
        """critic 返回 None → 不递归，直接终止 plan 为 failed。"""
        from packages.agent.planner import execute_plan_with_agent

        plan = _make_plan(
            goal="目标",
            steps=[{"id": "s1", "description": "步骤", "depends_on": []}],
        )

        async def fake_run(**kwargs):
            return {
                "final_message": "failed",
                "tool_calls": [],
                "steps": 1,
                "model": "m",
                "status": "failed",
            }

        async def fake_replan(**kwargs):
            return None  # Critic gives up

        async def _run_test():
            import packages.agent.plan_critic as pc_mod

            original_fn = pc_mod.replan_after_failure
            pc_mod.replan_after_failure = fake_replan
            try:
                result = await execute_plan_with_agent(
                    plan=plan,
                    tenant_id="t1",
                    session_id="s1",
                    allowed_tools=(),
                    allowed_models=(),
                    model=None,
                    session_store=self._make_session_store(),
                    run_agent_fn=fake_run,
                    max_replan_attempts=2,
                )
            finally:
                pc_mod.replan_after_failure = original_fn
            return result

        result = _run(_run_test())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["plan_revisions"], [])


if __name__ == "__main__":
    unittest.main()
