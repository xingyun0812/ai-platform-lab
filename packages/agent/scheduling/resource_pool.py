"""资源池隔离（PRD #243，ADR-0009 #244，#247）。

在工具执行前申领资源、执行后释放，保证并发有界、core 独占：

- **core**：独占池。同一池内同一时刻仅一个工具持有；其余等待（排队），互斥不被
  普通工具抢占（普通工具走 shared，不触碰 core 锁）。
- **shared**：限流池。``max_concurrent`` 上限 + 排队；默认所有未声明归属的工具归
  默认共享池，行为与现状一致（共享同一信号量、并发有界但不串行）。
- 未声明 ``resource_pool`` 的工具解析为 ``shared``（``SchedulingPolicy`` 默认），
  行为不变（向后兼容）。

占用/释放/等待事件以 ``ResourceEvent`` 落 trace（AC4）。

本模块纯内存 + asyncio 标准库，零外部依赖（不依赖 LLM/DB/yaml），可独立单测。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from packages.agent.scheduling.schedule_policy import SchedulingPolicy

DEFAULT_MAX_CONCURRENT = 5

# ResourceEvent.event 取值
WAIT_BEGIN = "wait_begin"
RESOURCE_ACQUIRED = "acquire"
RESOURCE_RELEASED = "release"


@dataclass(frozen=True)
class ResourceEvent:
    """一次资源等待/占用/释放事件的 trace 记录（AC4）。"""

    tool_name: str
    pool: str
    event: str  # wait_begin | acquire | release
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "pool": self.pool,
            "event": self.event,
            "detail": dict(self.detail),
        }


@dataclass
class ResourceHandle:
    """一次成功申领的句柄；用尽后 ``await handle.release()`` 归还并落释放事件。"""

    tool_name: str
    pool: str
    events: list[ResourceEvent] = field(default_factory=list)
    _release_fn: Callable[[], Awaitable[None]] | None = None

    async def release(self) -> ResourceEvent | None:
        if self._release_fn is None:
            return None
        fn, self._release_fn = self._release_fn, None
        await fn()
        ev = ResourceEvent(self.tool_name, self.pool, RESOURCE_RELEASED, {})
        self.events.append(ev)
        return ev


class ResourcePoolManager:
    """占用/释放追踪：core 独占（锁 + 排队），shared 限流（信号量 + 排队）。

    构造注入 ``resolve``（策略解析，生产传 ``store.resolve``）；测试可注入
    ``policy_map`` 桩。``max_concurrent`` 为 shared 池并发上限。
    """

    def __init__(
        self,
        resolve: Callable[[str], SchedulingPolicy] | None = None,
        *,
        policy_map: dict[str, SchedulingPolicy] | None = None,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        if policy_map is not None:
            # 未在 map 中的工具名按无约束默认策略处理，镜像 store.resolve 行为。
            def _resolve(name: str) -> SchedulingPolicy:
                return policy_map.get(name, SchedulingPolicy())

            self._resolve = _resolve
        elif resolve is not None:
            self._resolve = resolve
        else:
            raise ValueError("资源池管理器必须提供 resolve 或 policy_map")

        self._max_concurrent = max(1, int(max_concurrent))
        # core 独占锁：同一时刻仅一个 core 工具持有。
        self._core_lock = asyncio.Lock()
        # shared 限流信号量：所有 shared（含默认）工具共享，并发有界。
        self._shared_sem = asyncio.Semaphore(self._max_concurrent)

    def pool_for(self, tool_name: str) -> str:
        """解析工具归属的资源池（未声明默认 shared）。"""
        return self._resolve(tool_name).resource_pool

    async def acquire(self, tool_name: str) -> ResourceHandle:
        """申领资源：core 等待独占锁，shared 等待限流信号量。

        等待（wait_begin）与占用（acquire）事件记录在返回句柄的 ``events`` 上，
        持有期结束后 ``await handle.release()`` 落 release 事件。
        """
        pool = self.pool_for(tool_name)
        events: list[ResourceEvent] = []
        if pool == "core":
            events.append(ResourceEvent(tool_name, pool, WAIT_BEGIN, {"reason": "core_exclusive"}))
            await self._core_lock.acquire()

            async def _release() -> None:
                self._core_lock.release()

        else:
            events.append(ResourceEvent(tool_name, pool, WAIT_BEGIN, {"reason": "shared_limit"}))
            await self._shared_sem.acquire()

            async def _release() -> None:
                self._shared_sem.release()

        events.append(ResourceEvent(tool_name, pool, RESOURCE_ACQUIRED, {}))
        return ResourceHandle(tool_name=tool_name, pool=pool, events=events, _release_fn=_release)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent
