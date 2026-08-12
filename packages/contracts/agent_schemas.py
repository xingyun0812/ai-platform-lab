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
    plan_approval_id: str | None = Field(
        default=None,
        description="Phase Q Q4：Plan 审批通过后 resume 执行已批准的 Plan",
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
        description="react | cot | tot；缺省用 AGENT_REASONING_MODE / config/agent.yaml",
    )
    tot_config: TotConfig | None = Field(
        default=None,
        description="ToT 搜索配置（reasoning_mode='tot' 时必须）",
    )
    debate_config: DebateConfig | None = Field(
        default=None,
        description="Multi-Agent Debate 配置",
    )
    research_config: ResearchConfig | None = Field(
        default=None,
        description="Deep Research 配置",
    )
    self_refine_config: SelfRefineConfig | None = Field(
        default=None,
        description="Phase W: Self-Refine 配置",
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
    # Phase S: ToT
    tot_result: TotResult | None = Field(
        default=None,
        description="ToT 搜索结果（reasoning_mode='tot' 时返回）",
    )
    # Phase T: Debate
    debate_result: DebateResult | None = Field(
        default=None,
        description="Multi-Agent Debate 结果",
    )
    # Phase U: Research
    research_result: ResearchResult | None = Field(
        default=None,
        description="Deep Research 结果",
    )
    # Phase W: Self-Refine
    self_refine_result: SelfRefineResult | None = Field(
        default=None,
        description="Self-Refine 结果",
    )


# ---------------------------------------------------------------------------
# Phase S: Tree of Thoughts (ToT) 数据结构
# ---------------------------------------------------------------------------


class TotConfig(BaseModel):
    """ToT 搜索策略配置。"""
    enabled: bool = False
    search_algorithm: str = Field(default="bfs", description="bfs | dfs")
    branching_factor: int = Field(default=3, ge=1, le=10)
    beam_width: int = Field(default=2, ge=1, le=10)
    max_depth: int = Field(default=5, ge=1, le=20)
    max_total_nodes: int = Field(default=50, ge=1, le=500)
    value_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=120.0, ge=1.0)


class ThoughtNodeSchema(BaseModel):
    """ToT 思维节点（序列化用）。"""
    node_id: str
    state: str
    value: float | None = None
    status: str = "pending"
    parent_id: str | None = None
    children: list[str] = Field(default_factory=list)
    depth: int = 0
    visits: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThoughtTreeSchema(BaseModel):
    """ToT 思维树（序列化用）。"""
    root_id: str
    nodes: dict[str, ThoughtNodeSchema]
    goal: str
    total_nodes: int = 0
    max_depth: int = 0


class TotResult(BaseModel):
    """ToT 搜索结果。"""
    best_answer: str | None = None
    best_value: float = 0.0
    total_nodes: int = 0
    search_depth: int = 0
    execution_time_ms: float = 0.0
    tree: ThoughtTreeSchema | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Phase T: Multi-Agent Debate 数据结构
# ---------------------------------------------------------------------------


class DebateConfig(BaseModel):
    """Multi-Agent Debate 搜索策略配置。"""
    enabled: bool = True
    num_proposers: int = Field(default=3, ge=2, le=10, description="辩论 Agent 数量")
    num_rounds: int = Field(default=2, ge=1, le=3, description="辩论轮数（1=仅提案, 2=提案+评议, 3=含反驳）")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="proposer 生成温度")
    critic_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    judge_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=120.0, ge=1.0)
    proposer_model: str | None = None
    critic_model: str | None = None
    judge_model: str | None = None


class DebateProposalSchema(BaseModel):
    """辩论提案（序列化用）。"""
    agent_id: str
    proposal: str
    round_number: int
    confidence: float | None = None
    execution_time_ms: float = 0.0
    error: str | None = None


class DebateCritiqueSchema(BaseModel):
    """辩论评议（序列化用）。"""
    critic_agent_id: str
    target_agent_id: str
    critique: str
    round_number: int
    agreement: float | None = None
    error: str | None = None


class DebateResult(BaseModel):
    """辩论最终结果（序列化用）。"""
    question: str
    verdict: str
    verdict_confidence: float
    verdict_agent: str | None = None
    proposals: list[DebateProposalSchema] = Field(default_factory=list)
    critiques: list[DebateCritiqueSchema] = Field(default_factory=list)
    num_rounds_completed: int = 0
    execution_time_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Phase U: Deep Research 数据结构
# ---------------------------------------------------------------------------


class ResearchConfig(BaseModel):
    """Deep Research 配置。"""
    enabled: bool = True
    max_sub_questions: int = Field(default=5, ge=1, le=10)
    results_per_query: int = Field(default=5, ge=1, le=10)
    max_depth: int = Field(default=2, ge=1, le=5)
    timeout_seconds: float = Field(default=300.0, ge=1.0)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)


class ResearchNoteSchema(BaseModel):
    """研究笔记（序列化用）。"""
    sub_question: str
    source_url: str
    source_title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    error: str | None = None


class ResearchResult(BaseModel):
    """Deep Research 结果（序列化用）。"""
    question: str
    report: str
    sub_questions: list[str] = Field(default_factory=list)
    num_sources_consulted: int = 0
    depth_completed: int = 0
    execution_time_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Phase W: Self-Refine 数据结构
# ---------------------------------------------------------------------------


class SelfRefineConfig(BaseModel):
    """Self-Refine 策略配置。"""
    enabled: bool = True
    max_iterations: int = Field(default=5, ge=1, le=10)
    generator_model: str | None = None
    feedback_model: str | None = None
    convergence_strategy: str = Field(
        default="hybrid",
        pattern="^(llm_judged|similarity|hybrid)$",
    )
    convergence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_total_llm_calls: int = Field(default=15, ge=1, le=30)
    feedback_dimensions: list[str] | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=120.0, ge=1.0)


class FeedbackRoundSchema(BaseModel):
    """自反馈轮次记录（序列化用）。"""
    iteration: int
    feedback: str = ""
    feedback_dimension: str | None = None
    feedback_error: str | None = None
    refine_error: str | None = None
    output_after_refine: str = ""
    elapsed_ms: float = 0.0


class SelfRefineResult(BaseModel):
    """Self-Refine 最终结果（序列化用）。"""
    prompt: str
    final_output: str
    iterations_completed: int = 0
    converged: bool = False
    convergence_reason: str = ""
    trace: list[FeedbackRoundSchema] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    total_llm_calls: int = 0
    error: str | None = None
    success: bool = True
