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
        ...,
        min_length=1,
        description="本轮新增消息（通常一条 user），会拼接到 session 历史",
    )
    model: str | None = None
    kb_id: str | None = Field(
        default=None,
        description="供模型在 get_kb_snippet 中参考的默认 kb_id（写入 system 提示）",
    )


class ToolCallRecord(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    status: str  # success | failed | forbidden
    result: str | None = None
    error: str | None = None
    latency_ms: float = 0.0
    attempt: int = 0


class AgentRunResponse(BaseModel):
    tenant_id: str
    session_id: str
    final_message: str
    tool_calls: list[ToolCallRecord]
    steps: int
    model: str
    trace_id: str | None = None
