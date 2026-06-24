"""工具调用执行策略（Phase O #94）。"""

from __future__ import annotations

_TOOL_CALL_STRATEGIES = frozenset({"sequential", "parallel"})


class ToolCallStrategyError(ValueError):
    """无效 tool_call_strategy 配置。"""


def resolve_tool_call_strategy(
    request_strategy: str | None, settings_strategy: str | None
) -> str:
    raw = (request_strategy or settings_strategy or "sequential").strip().lower()
    if raw not in _TOOL_CALL_STRATEGIES:
        raise ToolCallStrategyError(f"unsupported tool_call_strategy: {raw}")
    return raw
