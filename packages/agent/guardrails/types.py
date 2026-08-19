"""Agent Production Guardrails — 共享类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StuckVerdict:
    """进度校验检测结果。stuck=True 表示检测到死循环。"""

    stuck: bool
    reason: str  # "empty_tools" | "identical_calls" | "output_loop" | "ok"
    detail: dict[str, Any] | None = None

    @classmethod
    def ok(cls) -> StuckVerdict:
        return cls(stuck=False, reason="ok")

    @classmethod
    def empty_tools(cls, count: int) -> StuckVerdict:
        return cls(stuck=True, reason="empty_tools", detail={"consecutive": count})

    @classmethod
    def identical_calls(cls, tool_name: str, count: int) -> StuckVerdict:
        return cls(
            stuck=True,
            reason="identical_calls",
            detail={"tool": tool_name, "consecutive": count},
        )

    @classmethod
    def output_loop(cls, similarity: float) -> StuckVerdict:
        return cls(stuck=True, reason="output_loop", detail={"similarity": similarity})


@dataclass(frozen=True)
class GuardrailVerdict:
    """熔断检测结果。triggered=True 表示触发熔断。"""

    triggered: bool
    layer: int  # 1-4
    reason: str  # "total_exceeded" | "tool_exceeded" | "timeout" | "stuck"
    detail: dict[str, Any] | None = None

    @classmethod
    def ok(cls) -> GuardrailVerdict:
        return cls(triggered=False, layer=0, reason="ok")

    @classmethod
    def total_exceeded(cls, limit: int, actual: int) -> GuardrailVerdict:
        return cls(
            triggered=True,
            layer=4,
            reason="total_exceeded",
            detail={"limit": limit, "actual": actual},
        )

    @classmethod
    def tool_exceeded(cls, tool: str, limit: int, actual: int) -> GuardrailVerdict:
        return cls(
            triggered=True,
            layer=4,
            reason="tool_exceeded",
            detail={"tool": tool, "limit": limit, "actual": actual},
        )

    @classmethod
    def timeout(cls, timeout_seconds: float) -> GuardrailVerdict:
        return cls(
            triggered=True,
            layer=4,
            reason="timeout",
            detail={"timeout_seconds": timeout_seconds},
        )

    @classmethod
    def stuck(cls, reason: str, detail: dict[str, Any] | None = None) -> GuardrailVerdict:
        return cls(
            triggered=True,
            layer=3,
            reason=f"stuck_{reason}",
            detail=detail,
        )
