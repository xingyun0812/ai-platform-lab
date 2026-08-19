"""ProgressTracker — 滑动窗口进度校验，检测死循环和无产出空转。"""

from __future__ import annotations

from collections import deque
from typing import Any

from packages.agent.guardrails.types import StuckVerdict


class ProgressTracker:
    """追踪最近 N 轮工具调用和 LLM 输出，检测死循环和无进度。

    Thread-safe: 否 — 每个 Agent run 创建独立实例。
    """

    def __init__(
        self,
        max_consecutive_empty_tools: int = 3,
        max_consecutive_identical_calls: int = 3,
        window_size: int = 10,
    ) -> None:
        self._max_empty = max_consecutive_empty_tools
        self._max_identical = max_consecutive_identical_calls
        self._window_size = window_size

        self._tool_history: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._llm_outputs: deque[str] = deque(maxlen=window_size)
        self._no_tool_rounds: int = 0

    def record_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        result: str | None = None,
    ) -> None:
        """记录一次工具调用。"""
        self._tool_history.append(
            {
                "tool": tool_name,
                "args": arguments or {},
                "result": result,
                "is_empty": not result or not result.strip(),
            }
        )
        self._no_tool_rounds = 0

    def record_no_tool_call(self) -> None:
        """记录一轮 LLM 未调用工具（直接返回文本）。"""
        self._no_tool_rounds += 1

    def record_llm_output(self, content: str) -> None:
        """记录 LLM 输出文本。"""
        self._llm_outputs.append(content)

    def check_stuck(self) -> StuckVerdict | None:
        """检查是否 stuck。返回 StuckVerdict 或 None（正常）。"""
        if not self._tool_history:
            return None

        # 1. 连续空结果
        empty_count = 0
        for entry in reversed(self._tool_history):
            if entry["is_empty"]:
                empty_count += 1
            else:
                break
        if empty_count >= self._max_empty:
            return StuckVerdict.empty_tools(empty_count)

        # 2. 同工具同参数重复
        if len(self._tool_history) >= self._max_identical:
            recent = list(self._tool_history)[-self._max_identical :]
            first = recent[0]
            if all(e["tool"] == first["tool"] and e["args"] == first["args"] for e in recent):
                return StuckVerdict.identical_calls(first["tool"], self._max_identical)

        return None

    @property
    def consecutive_no_tool_rounds(self) -> int:
        return self._no_tool_rounds

    @property
    def total_tool_calls(self) -> int:
        return len(self._tool_history)

    @property
    def recent_llm_outputs(self) -> list[str]:
        """获取最近 N 轮 LLM 输出列表。"""
        return list(self._llm_outputs)
