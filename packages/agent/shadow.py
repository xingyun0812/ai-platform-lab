from __future__ import annotations

from typing import Any

from packages.agent.tool_envelope import success_envelope
from packages.contracts.agent_schemas import ToolCallRecord


def shadow_tool_record(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[str, ToolCallRecord]:
    """Shadow 模式：记录拟执行参数，不触发真实副作用。"""
    payload = success_envelope(
        {
            "shadow": True,
            "tool_name": tool_name,
            "arguments": arguments,
            "executed": False,
        },
        quality_score=1.0,
    )
    record = ToolCallRecord(
        tool_name=tool_name,
        arguments=arguments,
        status="success",
        result=payload,
        error=None,
        latency_ms=0.0,
        quality_gate="skipped",
    )
    return payload, record
