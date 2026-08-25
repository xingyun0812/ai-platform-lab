#!/usr/bin/env python3
"""tests/test_mutex.py — 互斥裁决与串行回退（PRD #243，ADR-0009 #244，#246）。

只测外部行为：给定候选工具名 + 策略 → 得到正确 retain/defer/conflict。
纯单测，依赖 schedule_policy.SchedulingPolicy，零外部依赖（不依赖 LLM/DB）。
表驱动，镜像 tests/test_schedule_policy.py 风格。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.scheduling.mutex import MutexArbitrator  # noqa: E402
from packages.agent.scheduling.schedule_policy import SchedulingPolicy  # noqa: E402


def _policy(
    *,
    mutex_group: str | None = None,
    priority: int | None = None,
) -> SchedulingPolicy:
    return SchedulingPolicy(mutex_group=mutex_group, priority=priority)


class MutexArbitrateTest(unittest.TestCase):
    """候选集 → MutexDecision 的表驱动裁决。"""

    def _arb(self, policy_map: dict[str, SchedulingPolicy]) -> MutexArbitrator:
        return MutexArbitrator(policy_map=policy_map)

    def test_same_group_prioritized_keeps_highest(self):
        # 双方都显式声明 priority → 择优保留最高，其余推迟
        arb = self._arb(
            {"a": _policy(mutex_group="g", priority=10), "b": _policy(mutex_group="g", priority=50)}
        )
        d = arb.arbitrate(["a", "b"])
        self.assertEqual(d.keep, ("b",))
        self.assertEqual(d.deferred, ("a",))
        self.assertEqual(len(d.conflicts), 1)
        c = d.conflicts[0]
        self.assertEqual(c.reason, "priority_elected")
        self.assertEqual(c.mutex_group, "g")
        self.assertEqual(c.keep, ("b",))
        self.assertEqual(c.deferred, ("a",))
        self.assertEqual(set(c.involved), {"a", "b"})

    def test_same_group_any_undeclared_blocks_all(self):
        # 任一/双方未显式声明 priority → 全部拦截，不自动择优
        arb = self._arb({"a": _policy(mutex_group="g", priority=10), "b": _policy(mutex_group="g")})
        d = arb.arbitrate(["a", "b"])
        self.assertEqual(d.keep, ())
        self.assertEqual(set(d.deferred), {"a", "b"})
        self.assertEqual(d.conflicts[0].reason, "both_undeclared_blocked")

    def test_same_group_all_undeclared_blocks_all(self):
        arb = self._arb({"a": _policy(mutex_group="g"), "b": _policy(mutex_group="g")})
        d = arb.arbitrate(["a", "b"])
        self.assertEqual(d.keep, ())
        self.assertEqual(set(d.deferred), {"a", "b"})
        self.assertEqual(d.conflicts[0].reason, "both_undeclared_blocked")

    def test_same_group_tie_blocks_all(self):
        # 平手（多个并列最高）→ 无法择优，为保互斥不变量整组推迟
        arb = self._arb(
            {
                "a": _policy(mutex_group="g", priority=100),
                "b": _policy(mutex_group="g", priority=100),
            }
        )
        d = arb.arbitrate(["a", "b"])
        self.assertEqual(d.keep, ())
        self.assertEqual(set(d.deferred), {"a", "b"})
        self.assertEqual(d.conflicts[0].reason, "priority_tied")

    def test_no_declaration_passthrough(self):
        # 无 mutex_group → 透传，全部 keep，无冲突，无推迟
        arb = self._arb({"calc": _policy(), "sql": _policy()})
        d = arb.arbitrate(["calc", "sql"])
        self.assertEqual(d.keep, ("calc", "sql"))
        self.assertEqual(d.deferred, ())
        self.assertEqual(d.conflicts, ())

    def test_different_groups_independent(self):
        # 多组 → 交叉检查；不同组互不干扰
        arb = self._arb(
            {
                "del": _policy(mutex_group="d", priority=5),
                "drop": _policy(mutex_group="d", priority=5),
                "email": _policy(mutex_group="w", priority=2),
                "create": _policy(mutex_group="w", priority=9),
            }
        )
        d = arb.arbitrate(["del", "drop", "email", "create"])
        self.assertEqual(d.keep, ("create",))  # w 组 create(prio 9) 胜出
        self.assertEqual(set(d.deferred), {"del", "drop", "email"})
        self.assertEqual(len(d.conflicts), 2)
        reasons = {c.reason for c in d.conflicts}
        self.assertEqual(reasons, {"priority_tied", "priority_elected"})

    def test_multiplgroups_and_passthrough_mixed(self):
        # 有声明 + 无声明工具混排：无声明者透传 keep，与胜出者同跑
        arb = self._arb(
            {
                "a": _policy(mutex_group="g", priority=1),
                "b": _policy(mutex_group="g", priority=2),
                "free": _policy(),
            }
        )
        d = arb.arbitrate(["a", "b", "free"])
        self.assertEqual(d.keep, ("free", "b"))
        self.assertEqual(d.deferred, ("a",))
        self.assertEqual(len(d.conflicts), 1)

    def test_single_candidate_no_conflict(self):
        arb = self._arb({"a": _policy(mutex_group="g", priority=1)})
        d = arb.arbitrate(["a"])
        self.assertEqual(d.keep, ("a",))
        self.assertEqual(d.deferred, ())
        self.assertEqual(d.conflicts, ())

    def test_empty_candidates_no_conflict(self):
        arb = self._arb({})
        d = arb.arbitrate([])
        self.assertEqual(d.keep, ())
        self.assertEqual(d.deferred, ())
        self.assertEqual(d.conflicts, ())

    def test_lone_group_member_passthrough(self):
        # 一个互斥组内只有单成员 → 不构成冲突，透传 keep
        arb = self._arb({"a": _policy(mutex_group="g", priority=1)})
        d = arb.arbitrate(["a", "free"])
        # keep 顺序：无组透传在前，组内胜者/单成员在后 -> 用 set 断言（顺序无关）
        self.assertEqual(set(d.keep), {"a", "free"})
        self.assertEqual(d.deferred, ())
        self.assertEqual(d.conflicts, ())

    def test_retry_returns_deferred_for_next_round(self):
        # AC3：deferred 直接作为下一轮候选（串行回退复用）
        arb = self._arb(
            {"a": _policy(mutex_group="g", priority=1), "b": _policy(mutex_group="g", priority=2)}
        )
        d = arb.arbitrate(["a", "b"])
        retry = arb.retry(d.deferred)
        self.assertEqual(retry, d.deferred)


if __name__ == "__main__":
    unittest.main()
