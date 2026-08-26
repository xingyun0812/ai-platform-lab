"""ReflectionGate 网关放行/降级/拦截/去重/置信度升级单测（零外部依赖）。

遵循本仓库 async 测试约定：unittest.IsolatedAsyncioTestCase。
"""

from __future__ import annotations

import unittest

from packages.agent.reflection_gate import (
    TRIGGER_TASK_FAILURE,
    InMemoryDedupStore,
    NoopDedupStore,
    ReflectionGate,
    RuntimeDeps,
)
from packages.agent.reflection_metrics import ReflectionMetricsStore
from packages.agent.reflection_policy import ReflectionPolicy


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _RunningClock:
    """模拟随调用推进的时间（每次调用 +ms），用于驱动时延超时。"""

    def __init__(self, step_ms: float) -> None:
        self.t = 0.0
        self._step = step_ms

    def __call__(self) -> float:
        self.t += self._step
        return self.t


async def _checker(text: str = "ok", conf: float | None = 0.9):
    async def _fn(prompt: str):
        return text, conf

    return _fn


async def _full_deps(*, converge: bool = False) -> RuntimeDeps:
    async def checker(prompt: str) -> tuple[str, float | None]:
        return "refined", 0.9

    async def feedback(prompt: str, cur: str) -> str:
        return "needs more detail"

    async def conv(
        *,
        current_output: str,
        previous_output: str | None,
        latest_feedback: str,
        **_kwargs: object,
    ) -> tuple[bool, str]:
        return (True, "similarity") if converge else (False, "not_converged")

    return RuntimeDeps(
        small_checker=checker,
        feedback_fn=feedback,
        convergence_fn=conv,
        tokens_fn=lambda t: 4,
    )


class ReflectionGateTest(unittest.IsolatedAsyncioTestCase):
    def _gate(self, policy=None, *, deps=None, dedup=None) -> ReflectionGate:
        return ReflectionGate(policy=policy, deps=deps, dedup_store=dedup)

    # -- off / legacy：pass-through 零 LLM --------------------------------

    async def test_off_passes_through_zero_llm(self) -> None:
        stats = ReflectionMetricsStore()
        gate = self._gate(
            ReflectionPolicy(default_depth="off"),
            deps=RuntimeDeps(stats=stats, now_ms=_Clock()),
        )
        decision = await gate.decide(depth="off", trigger_event=TRIGGER_TASK_FAILURE)
        assert decision.action == "pass"
        assert decision.depth == "off"
        assert decision.tokens == 0
        assert decision.latency_ms == 0.0
        assert stats.total_tokens() == 0

    async def test_legacy_passes_through(self) -> None:
        gate = self._gate(ReflectionPolicy(default_depth="legacy"))
        decision = await gate.decide(depth="legacy")
        assert decision.action == "pass"
        assert decision.depth == "legacy"
        assert decision.tokens == 0

    async def test_default_depth_resolves_to_light(self) -> None:
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=await _checker("validated", 0.95)),
        )
        decision = await gate.decide()
        assert decision.depth == "light"
        assert decision.action == "check"

    # -- light：single-pass 小模型 + 置信度闸门 ---------------------------

    async def test_light_single_pass_check(self) -> None:
        text, conf = "output is correct", 0.95
        stats = ReflectionMetricsStore()
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(
                small_checker=await _checker(text, conf),
                stats=stats,
                tokens_fn=lambda t: 10,
                now_ms=_Clock(),
            ),
        )
        decision = await gate.decide(depth="light", trigger_event=TRIGGER_TASK_FAILURE)
        assert decision.action == "check"
        assert decision.rounds == 1
        assert decision.escalated is False
        assert decision.tokens == 10
        assert decision.output == text
        assert stats.count() == 1
        assert stats.by_reason()[TRIGGER_TASK_FAILURE] == 1

    async def test_light_low_confidence_escalates_to_big_model(self) -> None:
        small = await _checker("maybe wrong", 0.3)
        big = await _checker("confirmed correct", 0.99)
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=small, big_checker=big, tokens_fn=lambda t: 5),
        )
        decision = await gate.decide(depth="light")
        assert decision.escalated is True
        assert decision.output == "confirmed correct"

    async def test_light_small_check_failure_fails_open(self) -> None:
        async def _boom(prompt: str):
            raise RuntimeError("llm down")

        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=_boom),
        )
        decision = await gate.decide(depth="light")
        assert decision.action == "pass"
        assert "fail-open" in decision.reason

    # -- full：多轮迭代 + 收敛判停 + 三重兜底 -----------------------------

    async def test_full_iterates_until_convergence(self) -> None:
        stats = ReflectionMetricsStore()
        deps = await _full_deps(converge=True)
        deps.stats = stats
        deps.now_ms = _Clock()
        gate = self._gate(ReflectionPolicy(), deps=deps)
        decision = await gate.decide(depth="full", current_output="initial")
        assert decision.action == "check"
        assert decision.converged is True
        assert decision.rounds == 1
        assert decision.depth == "full"
        assert stats.count() == 1

    async def test_full_stops_on_max_iterations(self) -> None:
        deps = await _full_deps()
        gate = self._gate(ReflectionPolicy(), deps=deps)
        decision = await gate.decide(depth="full", current_output="initial")
        assert decision.converged is False
        assert decision.convergence_reason in (
            "max_iterations",
            "max_calls",
            "max_latency",
        )
        assert decision.rounds <= 5

    async def test_full_stops_on_max_latency(self) -> None:
        policy = ReflectionPolicy.from_dict({"full": {"max_total_latency_s": 0.001}})
        deps = await _full_deps()
        deps.now_ms = _RunningClock(step_ms=50.0)
        gate = self._gate(policy, deps=deps)
        decision = await gate.decide(depth="full", current_output="initial")
        assert decision.converged is False
        assert decision.convergence_reason == "max_latency"

    async def test_full_falls_back_to_light_without_iteration_deps(self) -> None:
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=await _checker("ok", 0.9)),
        )
        decision = await gate.decide(depth="full", current_output="initial")
        assert decision.depth == "light"

    # -- 去重：error_signature SHA256 hash 命中即跳过 ---------------------

    async def test_dedup_skips_on_repeat_signature(self) -> None:
        stats = ReflectionMetricsStore()
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=await _checker("ok", 0.9), stats=stats),
        )
        sig = "type_error:NoneType|step:3"
        first = await gate.decide(depth="light", error_signature=sig)
        assert first.deduped is False
        assert first.action != "skip"

        second = await gate.decide(depth="light", error_signature=sig)
        assert second.deduped is True
        assert second.action == "skip"
        assert second.tokens == 0

    async def test_dedup_different_signatures_do_not_skip(self) -> None:
        gate = self._gate(
            ReflectionPolicy(),
            deps=RuntimeDeps(small_checker=await _checker("ok", 0.9)),
        )
        a = await gate.decide(depth="light", error_signature="sig-a")
        b = await gate.decide(depth="light", error_signature="sig-b")
        assert a.deduped is False
        assert b.deduped is False
        assert a.action == "check"
        assert b.action == "check"

    async def test_dedup_disabled_when_policy_off(self) -> None:
        policy = ReflectionPolicy.from_dict({"dedup_enabled": False})
        gate = self._gate(
            policy,
            deps=RuntimeDeps(small_checker=await _checker("ok", 0.9)),
        )
        first = await gate.decide(depth="light", error_signature="same")
        second = await gate.decide(depth="light", error_signature="same")
        assert first.deduped is False
        assert second.deduped is False
        assert second.action == "check"


class DedupStoreTest(unittest.TestCase):
    def test_dedup_store_hash_behaviour(self) -> None:
        store = InMemoryDedupStore()
        assert store.contains("abc") is False
        store.add("abc")
        assert store.contains("abc") is True
        assert store.contains("abcd") is False
        noop = NoopDedupStore()
        assert noop.contains("anything") is False
