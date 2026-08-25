from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: ToolHandler
    # 调度元数据（ADR-0009 #244）：全可选，未声明视为无约束、向后兼容
    mutex_group: str | None = None
    priority: int | None = None
    resource_pool: str | None = None
    output_schema: str | None = None

    def openai_tool_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    def scheduling_metadata(self) -> dict[str, Any]:
        """工具注册侧声明的调度元数据（schedule_policy 双轨合并时作兜底）。"""
        return {
            "mutex_group": self.mutex_group,
            "priority": self.priority,
            "resource_pool": self.resource_pool,
            "output_schema": self.output_schema,
        }
