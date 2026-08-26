"""ReflectionGate 接入 self_refine / self_evolve 的深度路由单测（#256）。

使用 unittest.IsolatedAsyncioTestCase 遵循本仓库 async 测试约定；
LLM 经 tests/conftest.py 绑定 InMemoryPlatformPort（返回 mock 内容）。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from packages.agent.self_evolve import trigger_self_evolve
from packages.agent.self_refine import SelfRefineConfig, run_self_refine

_ORCH = "packages.agent.self_refine.orchestrator"


def _fake_llm_handler():
    """返回一个 AsyncMock，其调用会增量 counter 并返回字符串内容。

    与真实 `_call_llm` 一致：内部已解析 .status/.body/.choices 后返回
    `choices[0].message.content`（一个字符串）。generate/feedback/refine
    直接将其当字符串使用，故 mock 必须返回字符串而非 SimpleNamespace。
    """

    async def _handler(system, user=None, model=None, temperature=0.3, *, counter=None):
        if counter is not None:
            counter[0] += 1
        return "mock-output"

    return _handler


class SelfRefineReflectionDepthTest(unittest.IsolatedAsyncioTestCase):
    """run_self_refine 按 reflection_depth 路由。"""

    @patch(f"{_ORCH}._call_llm", new=_fake_llm_handler())
    async def test_off_routes_to_single_generation(self) -> None:
        cfg = SelfRefineConfig(reflection_depth="off")
        result = await run_self_refine("hello", config=cfg)
        assert result.iterations_completed == 0
        assert result.convergence_reason == "disabled"
        assert result.total_llm_calls == 1  # 仅生成一次，零反思迭代
        assert result.success

    @patch(f"{_ORCH}._call_llm", new=_fake_llm_handler())
    async def test_light_runs_single_pass(self) -> None:
        cfg = SelfRefineConfig(reflection_depth="light")
        result = await run_self_refine("hello", config=cfg)
        assert result.iterations_completed == 1
        assert result.convergence_reason == "light_single_pass"
        assert result.total_llm_calls == 3  # generate + feedback + refine
        assert result.success

    @patch(f"{_ORCH}._call_llm", new=_fake_llm_handler())
    async def test_legacy_preserves_existing_loop(self) -> None:
        # 默认 legacy：走既有多轮迭代 + 收敛逻辑。
        cfg = SelfRefineConfig()
        assert cfg.reflection_depth == "legacy"
        result = await run_self_refine("hello", config=cfg)
        assert result.success
        # 生成 + 至少一轮反馈 → total_llm_calls >= 2
        assert result.total_llm_calls >= 2

    @patch(f"{_ORCH}._call_llm", new=_fake_llm_handler())
    async def test_enabled_false_eq_off(self) -> None:
        cfg = SelfRefineConfig(enabled=False)
        result = await run_self_refine("hello", config=cfg)
        assert result.iterations_completed == 0
        assert result.convergence_reason == "disabled"
        assert result.total_llm_calls == 1


class SelfEvolveReflectionDepthTest(unittest.IsolatedAsyncioTestCase):
    """trigger_self_evolve 按 reflection_depth 门控。"""

    async def test_off_skips_entirely(self) -> None:
        result = await trigger_self_evolve(
            {"goal": "demo"},
            "success",
            reflection_depth="off",
        )
        # off → 完全跳过，不 store / 不反思 / 不 patch。
        assert result["experience_id"] is None
        assert result["lessons"] is None
        assert result["patch_id"] is None
        assert any("skipped" in e for e in result["errors"])
