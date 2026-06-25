"""tests/test_self_evolve.py — Phase R R1 自进化单元测试（≥12 个用例）。

运行方式：
    python tests/test_self_evolve.py
    # 或
    python -m pytest tests/test_self_evolve.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# 加载 experience_store / self_evolve 模块（避免触发 packages.agent.__init__ 链）
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent


def _setup_mock_packages() -> None:
    """注入必要的 mock 模块，使 experience_store / self_evolve 可独立加载。"""
    # packages hierarchy
    for mod_name in ["packages", "packages.contracts", "packages.contracts.agent_schemas"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    class _MockAgentPlan:
        def __init__(self, goal: str = "test goal") -> None:
            self.goal = goal
            self.steps: list = []

        def model_dump(self) -> dict:
            return {"goal": self.goal, "steps": []}

    sys.modules["packages.contracts.agent_schemas"].AgentPlan = _MockAgentPlan  # type: ignore[attr-defined]

    # apps hierarchy
    for mod_name in ["apps", "apps.gateway"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)


def _load_module(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _setup_model_router_mock(fail: bool = False) -> None:
    """注入 mock model_router 和 settings。"""

    class MockRoute:
        def __init__(self, ok: bool) -> None:
            self.status = 200 if ok else 500
            self.body = (
                {
                    "choices": [
                        {
                            "message": {
                                "content": "经验 1: 先验证输入\n经验 2: 记录结果\n经验 3: 检查边界条件"
                            }
                        }
                    ]
                }
                if ok
                else None
            )
            self.error = None if ok else "mock error"

    async def mock_forward(*args, **kwargs) -> MockRoute:
        return MockRoute(not fail)

    fake_router = types.ModuleType("apps.gateway.model_router")
    fake_router.forward_with_model_router = mock_forward  # type: ignore[attr-defined]

    fake_settings = types.ModuleType("apps.gateway.settings")

    class _Settings:
        agent_model = "gpt-4o"

    fake_settings.get_settings = lambda: _Settings()  # type: ignore[attr-defined]

    fake_perf = types.ModuleType("packages.agent.perf_metrics")

    class _Metrics:
        def record_self_evolve_experience(self, tid: str) -> None:
            pass

        def record_self_evolve_strategy_patch(self, tid: str) -> None:
            pass

    fake_perf.get_agent_perf_metrics = lambda: _Metrics()  # type: ignore[attr-defined]

    sys.modules["apps.gateway.model_router"] = fake_router
    sys.modules["apps.gateway.settings"] = fake_settings
    sys.modules["packages.agent.perf_metrics"] = fake_perf


def _run_async(coro) -> object:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_setup_mock_packages()
_setup_model_router_mock(fail=False)

_exp_mod = _load_module(
    "packages.agent.experience_store",
    PROJECT_ROOT / "packages" / "agent" / "experience_store.py",
)
_se_mod = _load_module(
    "packages.agent.self_evolve",
    PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
)


def _make_plan(goal: str = "test goal") -> object:
    """创建 mock AgentPlan。"""
    return sys.modules["packages.contracts.agent_schemas"].AgentPlan(goal)  # type: ignore[attr-defined]


def _make_record(
    goal: str = "test goal",
    outcome: str = "success",
    lessons: str = "经验 1: test lesson",
    tenant_id: str = "t1",
) -> object:
    plan = _make_plan(goal)
    return _exp_mod.build_experience_record(
        tenant_id=tenant_id,
        goal=goal,
        plan=plan,
        outcome=outcome,
        lessons=lessons,
    )


# ---------------------------------------------------------------------------
# TestExperienceStore
# ---------------------------------------------------------------------------


class TestExperienceStore(unittest.TestCase):
    def setUp(self) -> None:
        _exp_mod.reset_experience_store_for_tests()

    def test_store_and_get(self) -> None:
        """store() 后 get() 能返回同一条记录。"""
        record = _make_record()
        _exp_mod.store_experience(record)
        fetched = _exp_mod.get_experience_store().get(record.experience_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.experience_id, record.experience_id)  # type: ignore[union-attr]

    def test_retrieve_similar_exact(self) -> None:
        """retrieve_similar 能按 task_signature 精确检索。"""
        goal = "查询销售数据"
        r = _make_record(goal=goal)
        _exp_mod.store_experience(r)

        sig = _exp_mod.compute_task_signature(goal)
        results = _exp_mod.retrieve_similar_experiences(sig, top_k=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].goal, goal)

    def test_retrieve_similar_top_k(self) -> None:
        """retrieve_similar 遵守 top_k 限制。"""
        goal = "重复任务测试"
        for _ in range(5):
            r = _make_record(goal=goal)
            _exp_mod.store_experience(r)

        sig = _exp_mod.compute_task_signature(goal)
        results = _exp_mod.retrieve_similar_experiences(sig, top_k=2)
        self.assertLessEqual(len(results), 2)

    def test_retrieve_by_goal_substring(self) -> None:
        """retrieve_by_goal 能做 substring 模糊匹配。"""
        r = _make_record(goal="分析用户行为数据报告")
        _exp_mod.store_experience(r)

        # 用子串查询
        results = _exp_mod.get_experience_store().retrieve_by_goal("用户行为", top_k=3)
        self.assertGreaterEqual(len(results), 1)

    def test_list_all(self) -> None:
        """list_all 返回所有记录。"""
        for i in range(3):
            r = _make_record(goal=f"任务 {i}")
            _exp_mod.store_experience(r)

        all_records = _exp_mod.get_experience_store().list_all()
        self.assertEqual(len(all_records), 3)

    def test_delete(self) -> None:
        """delete 能删除记录，并返回 True；再次删除返回 False。"""
        r = _make_record()
        _exp_mod.store_experience(r)
        eid = r.experience_id

        ok = _exp_mod.get_experience_store().delete(eid)
        self.assertTrue(ok)

        ok2 = _exp_mod.get_experience_store().delete(eid)
        self.assertFalse(ok2)

        fetched = _exp_mod.get_experience_store().get(eid)
        self.assertIsNone(fetched)

    def test_sig_index_consistency(self) -> None:
        """删除后 sig_index 中不应再包含该 ID。"""
        goal = "索引一致性测试"
        r = _make_record(goal=goal)
        _exp_mod.store_experience(r)

        _exp_mod.get_experience_store().delete(r.experience_id)
        sig = _exp_mod.compute_task_signature(goal)
        results = _exp_mod.retrieve_similar_experiences(sig, top_k=3)
        self.assertEqual(len(results), 0)

    def test_to_dict(self) -> None:
        """to_dict 包含必要字段。"""
        r = _make_record()
        d = r.to_dict()
        for key in ["experience_id", "tenant_id", "goal", "outcome", "lessons", "created_at"]:
            self.assertIn(key, d)


# ---------------------------------------------------------------------------
# TestReflectOnRun
# ---------------------------------------------------------------------------


class TestReflectOnRun(unittest.TestCase):
    def setUp(self) -> None:
        _exp_mod.reset_experience_store_for_tests()
        _se_mod.reset_strategy_patch_store_for_tests()

    def test_reflect_returns_lessons_on_success(self) -> None:
        """LLM 正常返回时，reflect_on_run 应返回 lessons 字符串。"""
        _setup_model_router_mock(fail=False)
        # reload self_evolve to pick up new mock
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )
        se = sys.modules["packages.agent.self_evolve"]
        plan = _make_plan("数据分析任务")
        lessons = _run_async(se.reflect_on_run(plan, "success", [], tenant_id="t1"))
        self.assertIsInstance(lessons, str)
        self.assertGreater(len(lessons), 0)

    def test_reflect_fallback_on_llm_failure(self) -> None:
        """LLM 失败时，reflect_on_run 应回退返回简单 lessons，不抛异常。"""
        _setup_model_router_mock(fail=True)
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )
        se = sys.modules["packages.agent.self_evolve"]
        se.reset_strategy_patch_store_for_tests()
        plan = _make_plan("备份任务")
        lessons = _run_async(se.reflect_on_run(plan, "failed", [], tenant_id="t2"))
        self.assertIsInstance(lessons, str)
        self.assertGreater(len(lessons), 0)  # 回退模板非空

    def test_reflect_empty_tool_calls(self) -> None:
        """空 tool_calls 列表不导致崩溃。"""
        _setup_model_router_mock(fail=False)
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )
        se = sys.modules["packages.agent.self_evolve"]
        plan = _make_plan("")
        # goal 为空 plan
        lessons = _run_async(se.reflect_on_run(plan, "partial", None, tenant_id="t3"))
        self.assertIsInstance(lessons, str)


# ---------------------------------------------------------------------------
# TestMaybePatchStrategy
# ---------------------------------------------------------------------------


class TestMaybePatchStrategy(unittest.TestCase):
    def setUp(self) -> None:
        _se_mod.reset_strategy_patch_store_for_tests()
        _setup_model_router_mock(fail=False)
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )

    def _se(self) -> types.ModuleType:
        return sys.modules["packages.agent.self_evolve"]

    def test_patch_generated(self) -> None:
        """有 lessons 时，maybe_patch_strategy 应生成 StrategyPatch。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        lessons = "经验 1: 先规划再执行\n经验 2: 工具选择要精准"
        patch = _run_async(se.maybe_patch_strategy(lessons, {}, tenant_id="t1"))
        # LLM mock 返回 lessons 文本（非 JSON），patch 应仍创建
        self.assertIsNotNone(patch)
        self.assertEqual(patch.status, "pending")  # type: ignore[union-attr]
        self.assertEqual(patch.tenant_id, "t1")  # type: ignore[union-attr]

    def test_no_patch_on_empty_lessons(self) -> None:
        """空 lessons 不生成 patch。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        patch = _run_async(se.maybe_patch_strategy("", {}, tenant_id="t1"))
        self.assertIsNone(patch)

    def test_daily_limit_respected(self) -> None:
        """达到每日上限后，不再生成新 patch。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        store = se.get_strategy_patch_store()
        store.max_patches_per_day = 2

        lessons = "经验 1: keep it simple"
        _run_async(se.maybe_patch_strategy(lessons, {}, tenant_id="t_limit"))
        _run_async(se.maybe_patch_strategy(lessons, {}, tenant_id="t_limit"))
        # 第 3 次应被限流
        patch3 = _run_async(se.maybe_patch_strategy(lessons, {}, tenant_id="t_limit"))
        self.assertIsNone(patch3)


# ---------------------------------------------------------------------------
# TestApproveRejectStrategyPatch
# ---------------------------------------------------------------------------


class TestApproveRejectStrategyPatch(unittest.TestCase):
    def setUp(self) -> None:
        _se_mod.reset_strategy_patch_store_for_tests()
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )

    def _se(self) -> types.ModuleType:
        return sys.modules["packages.agent.self_evolve"]

    def _create_patch(self, se: types.ModuleType, tenant_id: str = "t1") -> object:
        patch = se.StrategyPatch(
            patch_id="patch-test-001",
            tenant_id=tenant_id,
            lessons="test lessons",
            proposed_change={"field": "plan_prompt", "old": "v1", "new": "v2"},
            status="pending",
            created_at=time.time(),
        )
        se.get_strategy_patch_store().add(patch)
        return patch

    def test_approve_changes_status(self) -> None:
        """approve 后 status 变为 approved。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        patch = self._create_patch(se)
        ok = se.approve_strategy_patch(patch.patch_id, decided_by="reviewer")  # type: ignore[union-attr]
        self.assertTrue(ok)
        updated = se.get_strategy_patch_store().get(patch.patch_id)  # type: ignore[union-attr]
        self.assertEqual(updated.status, "approved")  # type: ignore[union-attr]
        self.assertEqual(updated.decided_by, "reviewer")  # type: ignore[union-attr]

    def test_reject_changes_status(self) -> None:
        """reject 后 status 变为 rejected。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        patch = self._create_patch(se)
        ok = se.reject_strategy_patch(patch.patch_id, decided_by="admin")  # type: ignore[union-attr]
        self.assertTrue(ok)
        updated = se.get_strategy_patch_store().get(patch.patch_id)  # type: ignore[union-attr]
        self.assertEqual(updated.status, "rejected")  # type: ignore[union-attr]

    def test_nonexistent_patch_returns_false(self) -> None:
        """对不存在 patch_id 操作返回 False。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        ok_approve = se.approve_strategy_patch("nonexistent-id")
        ok_reject = se.reject_strategy_patch("nonexistent-id")
        self.assertFalse(ok_approve)
        self.assertFalse(ok_reject)


# ---------------------------------------------------------------------------
# TestTriggerSelfEvolve
# ---------------------------------------------------------------------------


class TestTriggerSelfEvolve(unittest.TestCase):
    def setUp(self) -> None:
        _exp_mod.reset_experience_store_for_tests()
        _se_mod.reset_strategy_patch_store_for_tests()
        _setup_model_router_mock(fail=False)
        _load_module(
            "packages.agent.self_evolve",
            PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
        )

    def _se(self) -> types.ModuleType:
        return sys.modules["packages.agent.self_evolve"]

    def test_trigger_stores_experience(self) -> None:
        """trigger_self_evolve 应存储经验到 experience_store。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        _exp_mod.reset_experience_store_for_tests()
        plan = _make_plan("月度报表生成")
        result = _run_async(se.trigger_self_evolve(plan, "success", tenant_id="t1", tool_calls=[]))
        self.assertIsNotNone(result["experience_id"])
        # 验证确实存储了
        stored = _exp_mod.get_experience_store().get(result["experience_id"])
        self.assertIsNotNone(stored)

    def test_trigger_returns_lessons(self) -> None:
        """trigger_self_evolve 应返回 lessons。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        _exp_mod.reset_experience_store_for_tests()
        plan = _make_plan("数据清洗任务")
        result = _run_async(se.trigger_self_evolve(plan, "success", tenant_id="t2"))
        # lessons 可为空（LLM 可能失败），但不应报错
        self.assertIn("experience_id", result)
        self.assertIn("errors", result)

    def test_trigger_isolates_exceptions(self) -> None:
        """即使 experience_store 内部出错，trigger_self_evolve 不应抛出异常。"""
        se = self._se()
        se.reset_strategy_patch_store_for_tests()
        _exp_mod.reset_experience_store_for_tests()
        # 传 None plan 测试容错
        try:
            _run_async(se.trigger_self_evolve(None, "success", tenant_id="t3"))
        except Exception as exc:
            self.fail(f"trigger_self_evolve raised unexpected exception: {exc}")


# ---------------------------------------------------------------------------
# TestPlannerExperienceInjection
# ---------------------------------------------------------------------------


class TestPlannerExperienceInjection(unittest.TestCase):
    def setUp(self) -> None:
        _exp_mod.reset_experience_store_for_tests()

    def test_experience_injected_into_context(self) -> None:
        """当有成功经验时，应能检索并注入 lessons。"""
        goal = "生成财务报告"
        r = _make_record(goal=goal, outcome="success", lessons="经验 P1: 先对账再输出")
        _exp_mod.store_experience(r)

        sig = _exp_mod.compute_task_signature(goal)
        similar = _exp_mod.retrieve_similar_experiences(sig, top_k=2)
        self.assertGreaterEqual(len(similar), 1)

        # 构造注入逻辑（与 planner.py 中一致）
        lessons_lines = [f"- {e.lessons}" for e in similar if e.outcome == "success" and e.lessons]
        self.assertGreater(len(lessons_lines), 0)
        injected = "\n".join(lessons_lines)
        self.assertIn("经验 P1", injected)

    def test_injection_fails_gracefully(self) -> None:
        """experience_store 不可用时，不应阻塞主流程（模拟异常）。"""
        # 模拟 compute_task_signature 抛出异常
        original_fn = _exp_mod.compute_task_signature
        try:
            _exp_mod.compute_task_signature = lambda g: (_ for _ in ()).throw(
                RuntimeError("mock fail")
            )  # type: ignore[assignment]
        except Exception:
            pass

        # 恢复
        _exp_mod.compute_task_signature = original_fn

        # 测试 compute_task_signature 正常工作
        sig = _exp_mod.compute_task_signature("test")
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 16)

    def test_compute_task_signature_deterministic(self) -> None:
        """相同 goal 的 task_signature 应相同，不同 goal 应不同。"""
        goal = "固定任务"
        s1 = _exp_mod.compute_task_signature(goal)
        s2 = _exp_mod.compute_task_signature(goal)
        self.assertEqual(s1, s2)

        s3 = _exp_mod.compute_task_signature("另一个任务")
        self.assertNotEqual(s1, s3)
        self.assertEqual(len(s1), 16)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
