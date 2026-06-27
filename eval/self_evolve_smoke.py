"""eval/self_evolve_smoke.py — Phase R R1 自进化 smoke 测试。

场景：同类任务跑 2 次，第 2 次应能从经验库检索到第 1 次的经验。

运行方式：
    python3 eval/self_evolve_smoke.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Setup mock modules (必须在 exec_module 之前注入到 sys.modules)
# ---------------------------------------------------------------------------


def _setup_mocks() -> None:
    for mod_name in ["packages", "packages.contracts", "packages.contracts.agent_schemas"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    class MockAgentPlan:
        def __init__(self, goal: str = "test") -> None:
            self.goal = goal
            self.steps: list = []

        def model_dump(self) -> dict:
            return {"goal": self.goal, "steps": []}

    sys.modules["packages.contracts.agent_schemas"].AgentPlan = MockAgentPlan  # type: ignore[attr-defined]

    for mod_name in ["apps", "apps.gateway"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    fake_router = types.ModuleType("apps.gateway.model_router")

    class MockRoute:
        status = 500
        body = None
        error = "mock no LLM"

    async def mock_forward(*args, **kwargs) -> MockRoute:
        return MockRoute()

    fake_router.forward_with_model_router = mock_forward  # type: ignore[attr-defined]
    sys.modules["apps.gateway.model_router"] = fake_router

    fake_settings = types.ModuleType("apps.gateway.settings")

    class MockSettings:
        agent_model = "gpt-4o"

    fake_settings.get_settings = lambda: MockSettings()  # type: ignore[attr-defined]
    sys.modules["apps.gateway.settings"] = fake_settings

    fake_perf = types.ModuleType("packages.agent.perf_metrics")

    class MockMetrics:
        def record_self_evolve_experience(self, tid: str) -> None:
            pass

        def record_self_evolve_strategy_patch(self, tid: str) -> None:
            pass

    fake_perf.get_agent_perf_metrics = lambda: MockMetrics()  # type: ignore[attr-defined]
    sys.modules["packages.agent.perf_metrics"] = fake_perf


def _load_module(name: str, path: Path) -> types.ModuleType:
    """加载模块并注册到 sys.modules（Python 3.9 dataclass 兼容）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # CRITICAL: 必须先注册到 sys.modules，再 exec_module（Python 3.9 dataclass 需要）
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


_setup_mocks()

_exp_mod = _load_module(
    "packages.agent.experience_store",
    PROJECT_ROOT / "packages" / "agent" / "experience_store.py",
)
_se_mod = _load_module(
    "packages.agent.self_evolve",
    PROJECT_ROOT / "packages" / "agent" / "self_evolve.py",
)

MockAgentPlan = sys.modules["packages.contracts.agent_schemas"].AgentPlan  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Smoke test scenarios
# ---------------------------------------------------------------------------


async def test_experience_roundtrip() -> None:
    """场景 1：存储经验 → 检索到经验。"""
    _exp_mod.reset_experience_store_for_tests()

    goal = "查询销售数据并生成报告"
    plan = MockAgentPlan(goal)
    record = _exp_mod.build_experience_record(
        tenant_id="t1",
        goal=goal,
        plan=plan,
        tool_calls=[{"tool": "sql_query", "args": {"sql": "SELECT * FROM sales"}}],
        outcome="success",
        lessons="经验 1: 先确认数据库连接\n经验 2: 输出前做数据校验",
    )
    await _exp_mod.store_experience(record)

    sig = _exp_mod.compute_task_signature(goal)
    similar = await _exp_mod.retrieve_similar_experiences(sig, top_k=3)
    assert len(similar) >= 1, f"Expected ≥1 similar experience, got {len(similar)}"
    assert similar[0].goal == goal
    assert "经验 1" in similar[0].lessons

    print(f"✅ test_experience_roundtrip passed — found {len(similar)} experience(s)")


async def test_second_run_injects_experience() -> None:
    """场景 2：第 2 次相同任务，assert 注入了历史经验到 plan 上下文。"""
    _exp_mod.reset_experience_store_for_tests()

    goal = "分析用户行为数据"
    plan = MockAgentPlan(goal)

    r = _exp_mod.build_experience_record(
        tenant_id="t2",
        goal=goal,
        plan=plan,
        outcome="success",
        lessons="经验 A: 先聚合再分析\n经验 B: 注意 null 值处理",
    )
    await _exp_mod.store_experience(r)

    sig = _exp_mod.compute_task_signature(goal)
    similar = await _exp_mod.retrieve_similar_experiences(sig, top_k=2)
    assert len(similar) >= 1

    injected_context = "\n".join(f"- {e.lessons}" for e in similar if e.outcome == "success")
    assert "经验 A" in injected_context, f"lessons not injected: {injected_context}"

    print(
        f"✅ test_second_run_injects_experience passed — lessons injected: {injected_context[:80]}"
    )


async def test_trigger_self_evolve_mock() -> None:
    """场景 3：mock trigger_self_evolve 在无 LLM 下不崩溃，经验入库。"""
    _exp_mod.reset_experience_store_for_tests()
    _se_mod.reset_strategy_patch_store_for_tests()

    plan = MockAgentPlan("统计月度收入")
    result = await _se_mod.trigger_self_evolve(
        plan,
        "success",
        tenant_id="t3",
        tool_calls=[],
    )
    assert result["experience_id"] is not None, "experience should be stored"

    stored = await _exp_mod.get_experience_store().get(result["experience_id"])
    assert stored is not None, "experience should be retrievable"
    assert stored.tenant_id == "t3"

    print(f"✅ test_trigger_self_evolve_mock passed — experience_id={result['experience_id']}")


async def _run_all() -> None:
    await test_experience_roundtrip()
    await test_second_run_injects_experience()
    await test_trigger_self_evolve_mock()


def main() -> None:
    print("=" * 60)
    print("Phase R R1 Self-Evolving Agent — Smoke Tests")
    print("=" * 60)

    asyncio.run(_run_all())

    print("\n✅ All smoke tests passed!")


if __name__ == "__main__":
    main()
