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
    auto_plan: bool = Field(
        default=False,
        description="Phase O #87：先生成 Plan 再逐步执行",
    )
    goal: str | None = Field(
        default=None,
        description="auto_plan 时的任务目标；缺省取最后一条 user 消息",
    )
    require_plan_approval: bool = Field(
        default=False,
        description="Phase Q Q4：auto_plan 时先生成 Plan 并暂停 plan 级审批",
    )
    reasoning_mode: str | None = Field(
        default=None,
        description="react | cot；缺省用 AGENT_REASONING_MODE / config/agent.yaml",
    )


class PlanStep(BaseModel):
    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    tool_hint: str | None = None
    agent_hint: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class AgentPlan(BaseModel):
    goal: str = Field(..., min_length=1)
    steps: list[PlanStep] = Field(..., min_length=1)


class AgentPlanRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    context: str | None = Field(
        default=None,
        description="可选背景（memory/RAG 摘要等）",
    )
    model: str | None = None


class AgentPlanResponse(BaseModel):
    tenant_id: str
    goal: str
    plan: AgentPlan
    model: str
    trace_id: str | None = None


class ReasoningTraceRecord(BaseModel):
    step: int
    thinking: str | None = None
    visible_content: str | None = None


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
    plan_approval_id: str | None = None
    plan_summary: str | None = None
    plan_revisions: list[dict[str, Any]] | None = None
    plan: AgentPlan | None = None
    plan_steps_completed: int | None = None
    reasoning_mode: str | None = None
    reasoning_trace: list[ReasoningTraceRecord] | None = None
    shadow_tool_calls: list[ToolCallRecord] | None = None
