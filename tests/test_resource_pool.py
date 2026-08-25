#!/usr/bin/env python3
"""tests/test_resource_pool.py — 资源池隔离（PRD #243，ADR-0009 #244，#247）。

只测外部行为：给定工具名 + 策略 → 得到正确的 core/shared 占用、限流、排队与事件。
纯单测，依赖 schedule_policy.SchedulingPolicy，零外部依赖（不依赖 LLM/DB）。
镜像 tests/test_mutex.py 风格；async 用 asyncio.run 驱动。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.scheduling.resource_pool import (  # noqa: E402
    RESOURCE_ACQUIRED,
    RESOURCE_RELEASED,
    WAIT_BEGIN,
    ResourcePoolManager,
)
from packages.agent.scheduling.schedule_policy import SchedulingPolicy  # noqa: E402


def _policy(*, resource_pool: str = "shared") -> SchedulingPolicy:
    return SchedulingPolicy(resource_pool=resource_pool)


# 用 wait_for 探测协程是否被阻塞；阻塞则抛 TimeoutError。
async def _probe(coro):
    """返回 (completed: bool, result)。协程在 short timeout 内未完成 → completed=False。"""
    task = asyncio.ensure_future(coro)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
        return True, result
    except TimeoutError:
        return False, task


class ResourcePoolCoreTest(unittest.TestCase):
    """core 独占：同一时刻仅一个工具持有，冲突串行。"""

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_core_exclusive_serializes_holders(self):
        """两个 core 工具不能同时持有：第二个必须等第一个释放。"""
        pm = ResourcePoolManager(
            policy_map={"a": _policy(resource_pool="core"), "b": _policy(resource_pool="core")}
        )
        order: list[str] = []

        async def run():
            h1 = await pm.acquire("a")
            order.append("a_acquired")
            done, b_result = await _probe(pm.acquire("b"))
            self.assertFalse(done, "core b 不应在 a 释放前获得")
            order.append("b_blocked")
            await h1.release()
            h2 = await b_result
            order.append("b_acquired_after_release")
            await h2.release()

        self.run_async(run())
        self.assertTrue(order.index("b_blocked") < order.index("b_acquired_after_release"))

    def test_core_not_preempted_by_shared(self):
        """共享工具不抢占 core：core 工具可随时获取，不受 shared 占用影响。"""
        pm = ResourcePoolManager(
            policy_map={
                "core": _policy(resource_pool="core"),
                "sh": _policy(resource_pool="shared"),
            }
        )

        async def run():
            h1 = await pm.acquire("sh")
            try:
                done, h2_result = await _probe(pm.acquire("core"))
                self.assertTrue(done, "core 应立即拿到，不被 shared 占用阻塞")
                h2 = h2_result
                await h2.release()
            finally:
                await h1.release()

        self.run_async(run())


class ResourcePoolSharedTest(unittest.TestCase):
    """shared 限流 + 排队，并发有界。"""

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_shared_limits_concurrency(self):
        """并发占用数不超过 max_concurrent，超出者排队直至放行。"""
        pm = ResourcePoolManager(
            policy_map={f"t{i}": _policy() for i in range(4)}, max_concurrent=2
        )
        peak = 0

        async def run():
            nonlocal peak
            handles = await asyncio.gather(*(pm.acquire(f"t{i}") for i in range(4)))
            # 所有 4 个都能最终拿到（排队而非失败）
            peak = len(handles)
            for h in handles:
                await h.release()

        self.run_async(run())
        self.assertEqual(peak, 4)

    def test_shared_waits_for_release(self):
        """第 max_concurrent+1 个工具必须等前面释放才可获取（排队）。"""
        pm = ResourcePoolManager(policy_map={"a": _policy(), "b": _policy()}, max_concurrent=1)
        order: list[str] = []

        async def run():
            h1 = await pm.acquire("a")
            order.append("a_held")
            done, b_result = await _probe(pm.acquire("b"))
            self.assertFalse(done, "shared b 不应在信号量释放前获得")
            order.append("b_blocked")
            await h1.release()
            h2 = await b_result
            order.append("b_after_release")
            await h2.release()

        self.run_async(run())
        self.assertTrue(order.index("b_blocked") < order.index("b_after_release"))


class ResourcePoolEventsTest(unittest.TestCase):
    """占用/释放/等待事件落 trace（AC4）。"""

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_acquire_release_events_recorded(self):
        pm = ResourcePoolManager(policy_map={"a": _policy(resource_pool="core")})

        async def run():
            h = await pm.acquire("a")
            evs = list(h.events)
            re = await h.release()
            evs.extend([re] if re else [])
            return evs

        evs = self.run_async(run())
        events = [e.event for e in evs]
        self.assertEqual(events, [WAIT_BEGIN, RESOURCE_ACQUIRED, RESOURCE_RELEASED])
        self.assertEqual(evs[0].pool, "core")
        self.assertEqual(evs[0].detail.get("reason"), "core_exclusive")

    def test_default_shared_for_undeclared(self):
        """未声明归属的工具归默认 shared 池（行为与现状一致）。"""
        pm = ResourcePoolManager(policy_map={"free": _policy()})
        self.assertEqual(pm.pool_for("free"), "shared")

    def test_shared_events_include_reason(self):
        pm = ResourcePoolManager(policy_map={"a": _policy(resource_pool="shared")})

        async def run():
            h = await pm.acquire("a")
            evs = list(h.events)
            re = await h.release()
            evs.extend([re] if re else [])
            return evs

        evs = self.run_async(run())
        self.assertEqual(evs[0].event, WAIT_BEGIN)
        self.assertEqual(evs[0].detail.get("reason"), "shared_limit")


if __name__ == "__main__":
    unittest.main()
