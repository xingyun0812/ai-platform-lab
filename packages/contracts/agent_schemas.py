from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    role: str
    content: str | None = None


class AgentRunRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    messages: list[AgentMessage] = Field(
        default_factory=list,
        description="本轮新增消息；resume 已确认 approval 时可省略",
    )
    model: str | None = None
    kb_id: str | None = Field(
        default=None,
        description="供模型在 get_kb_snippet 中参考的默认 kb_id（写入 system 提示）",
    )
    approval_id: str | None = Field(
        default=None,
        description="Phase E5：人工确认后 resume 执行已批准的工具调用",
    )


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    status: str  # success | failed | forbidden
    result: str | None = None
    error: str | None = None
    latency_ms: float = 0.0
    attempt: int = 0
    quality_gate: str | None = None  # passed | low_quality | skipped | failed


class AgentRunResponse(BaseModel):
    tenant_id: str
    session_id: str
    final_message: str
    tool_calls: list[ToolCallRecord]
    steps: int
    model: str
    trace_id: str | None = None
    status: str = "completed"
    approval_id: str | None = None
    shadow_tool_calls: list[ToolCallRecord] | None = None
