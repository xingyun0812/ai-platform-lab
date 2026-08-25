"""互斥裁决与串行回退（PRD #243，ADR-0009 #244，#246）。

在工具并行调度前拦截互斥组，按 ADR-0009「决策 2」语义择优/推迟：

- 同组候选 ≥2 个同时出现 → 冲突。
- **双方都显式声明了 ``priority``** → 保留最高优先级者（平手视为无法择优），
  其余推迟（下一轮重试）。
- **任一/双方未显式声明 ``priority``** → 全部拦截（全部推迟该轮，不自动择优）。

互斥裁决后产物：``MutexDecision{keep, deferred, conflicts}``。
本模块纯数据/判定，只依赖 ``schedule_policy.SchedulingPolicy``，不依赖 LLM/DB。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from packages.agent.scheduling.schedule_policy import SchedulingPolicy


@dataclass(frozen=True)
class MutexConflict:
    """同一互斥组内的一次冲突仲裁结果。"""

    mutex_group: str
    involved: tuple[str, ...]
    keep: tuple[str, ...]
    deferred: tuple[str, ...]
    reason: str  # priority_elected | priority_tied | both_undeclared_blocked


@dataclass(frozen=True)
class MutexDecision:
    """一轮候选的互斥裁决结果。"""

    keep: tuple[str, ...]
    deferred: tuple[str, ...]
    conflicts: tuple[MutexConflict, ...] = ()


class MutexArbitrator:
    """互斥仲裁器。构造注入 ``resolve``（策略解析函数，生产传 ``store.resolve``）。

    零依赖纯函数：给定候选工具名集合 → 产出保留/推迟/冲突明细，便于表驱动单测。
    """

    def __init__(
        self,
        resolve: Callable[[str], SchedulingPolicy] | None = None,
        *,
        policy_map: dict[str, SchedulingPolicy] | None = None,
    ) -> None:
        if policy_map is not None:
            # 未在 map 中的工具名按无约束默认策略处理，镜像 store.resolve 行为。
            def _resolve(name: str) -> SchedulingPolicy:
                return policy_map.get(name, SchedulingPolicy())

            self._resolve = _resolve
        elif resolve is not None:
            self._resolve = resolve
        else:
            raise ValueError("mutex 仲裁器必须提供 resolve 或 policy_map")

    def arbitrate(self, candidate_names: Iterable[str]) -> MutexDecision:
        """对一组候选工具名进行互斥裁决。

        Returns:
            MutexDecision：keep 可并行执行，deferred 推迟到下一轮，conflicts 为明细。
        """
        names = list(candidate_names)
        if len(names) <= 1:
            # 单候选或无候选：天然无同组冲突，透传。
            return MutexDecision(keep=tuple(names), deferred=())

        # 按互斥组分桶；未声明 mutex_group 的工具不参与（向后兼容，透传）。
        by_group: dict[str, list[str]] = {}
        no_group: list[str] = []
        policies: dict[str, SchedulingPolicy] = {}
        for name in names:
            policy = self._resolve(name)
            policies[name] = policy
            if policy.mutex_group:
                by_group.setdefault(policy.mutex_group, []).append(name)
            else:
                no_group.append(name)

        keep: list[str] = list(no_group)
        deferred: list[str] = []
        conflicts: list[MutexConflict] = []

        for group, members in by_group.items():
            if len(members) <= 1:
                keep.extend(members)
                continue
            conflict = self._arbitrate_group(group, members, policies)
            conflicts.append(conflict)
            keep.extend(conflict.keep)
            deferred.extend(conflict.deferred)

        return MutexDecision(
            keep=tuple(keep),
            deferred=tuple(deferred),
            conflicts=tuple(conflicts),
        )

    def _arbitrate_group(
        self,
        group: str,
        members: list[str],
        policies: dict[str, SchedulingPolicy],
    ) -> MutexConflict:
        """裁决单个互斥组的冲突（ADR-0009 决策 2）。"""
        declared = [(m, policies[m].priority) for m in members]
        priorities = [p for _, p in declared]

        if any(p is None for p in priorities):
            # 任一/双方未显式声明 → 全部拦截，不自动择优（避免静默改变意图）。
            return MutexConflict(
                mutex_group=group,
                involved=tuple(members),
                keep=(),
                deferred=tuple(members),
                reason="both_undeclared_blocked",
            )

        # 此时 all(p is not None) 成立
        non_null: list[int] = [p for p in priorities if p is not None]
        max_prio = max(non_null)
        highest = [m for m, p in declared if p == max_prio]

        if len(highest) == 1:
            # 唯一最高优先级者胜出，其余推迟下一轮。
            return MutexConflict(
                mutex_group=group,
                involved=tuple(members),
                keep=(highest[0],),
                deferred=tuple(m for m in members if m != highest[0]),
                reason="priority_elected",
            )

        # 平手（多个并列最高）：无法自动择优；为保住互斥不变量，整组推迟。
        return MutexConflict(
            mutex_group=group,
            involved=tuple(members),
            keep=(),
            deferred=tuple(members),
            reason="priority_tied",
        )

    def retry(self, deferred_names: Iterable[str]) -> tuple[str, ...]:
        """将推迟列表转为下一轮候选（供串行回退复用，AC3）。"""
        return tuple(deferred_names)
