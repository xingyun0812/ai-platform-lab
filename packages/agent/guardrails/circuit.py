"""ThresholdEnforcer — 工具调用计数器，支持全局上限和按工具类型上限。"""

from __future__ import annotations

import time
from collections import defaultdict

from packages.agent.guardrails.types import GuardrailVerdict


class ThresholdEnforcer:
    """熔断阈值计数器。每个 Agent run 创建独立实例。

    检查三种阈值：
    - 工具调用总次数上限
    - 单工具类型调用次数上限
    - 执行超时
    """

    def __init__(
        self,
        max_tool_calls_total: int = 30,
        tool_call_limits: dict[str, int] | None = None,
        agent_timeout_seconds: float = 300.0,
    ) -> None:
        self._max_total = max_tool_calls_total
        self._tool_limits = dict(tool_call_limits or {})
        self._timeout = agent_timeout_seconds

        self._tool_counts: dict[str, int] = defaultdict(int)
        self._total_count: int = 0
        self._start_time: float = time.time()

    def check_tool_call(self, tool_name: str) -> GuardrailVerdict:
        """检查是否超过阈值。

        必须在工具调用前调用（预检查），不要等工具执行完才检查。
        """
        # Check total
        next_total = self._total_count + 1
        if next_total > self._max_total:
            return GuardrailVerdict.total_exceeded(self._max_total, self._total_count)

        # Check per-tool
        next_tool = self._tool_counts.get(tool_name, 0) + 1
        tool_limit = self._tool_limits.get(tool_name, self._max_total)
        if next_tool > tool_limit:
            return GuardrailVerdict.tool_exceeded(
                tool_name, tool_limit, self._tool_counts.get(tool_name, 0)
            )

        return GuardrailVerdict.ok()

    def record_tool_call(self, tool_name: str) -> None:
        """记录实际工具调用（调用 check 通过后再调用）。"""
        self._tool_counts[tool_name] += 1
        self._total_count += 1

    def check_timeout(self) -> GuardrailVerdict:
        """检查是否超时。"""
        elapsed = time.time() - self._start_time
        if elapsed > self._timeout:
            return GuardrailVerdict.timeout(self._timeout)
        return GuardrailVerdict.ok()

    @property
    def total_count(self) -> int:
        return self._total_count

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def tool_counts(self) -> dict[str, int]:
        return dict(self._tool_counts)
