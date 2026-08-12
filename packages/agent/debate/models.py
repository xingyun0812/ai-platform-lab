from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DebateConfig:
    """Multi-Agent Debate 策略配置。"""

    enabled: bool = True
    num_proposers: int = 3  # 辩论 Agent 数量
    num_rounds: int = 2  # 辩论轮数（1=仅提案, 2=提案+评议, 3=含反驳）
    temperature: float = 0.7  # proposer 生成温度
    critic_temperature: float = 0.3  # critic 评议温度
    judge_temperature: float = 0.1  # judge 裁定温度
    timeout_seconds: float = 120.0
    proposer_model: str | None = None
    critic_model: str | None = None
    judge_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "num_proposers": self.num_proposers,
            "num_rounds": self.num_rounds,
            "temperature": self.temperature,
            "critic_temperature": self.critic_temperature,
            "judge_temperature": self.judge_temperature,
            "timeout_seconds": self.timeout_seconds,
            "proposer_model": self.proposer_model,
            "critic_model": self.critic_model,
            "judge_model": self.judge_model,
        }


@dataclass
class DebateProposal:
    """辩论提案（proposer 的输出）。"""

    agent_id: str
    proposal: str
    round_number: int
    confidence: float | None = None
    execution_time_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "proposal": self.proposal,
            "round_number": self.round_number,
            "confidence": self.confidence,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
        }


@dataclass
class DebateCritique:
    """辩论评议（critic 对某个 proposer 的评审）。"""

    critic_agent_id: str
    target_agent_id: str
    critique: str
    round_number: int
    agreement: float | None = None  # 0-1 对提案的认可度
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "critic_agent_id": self.critic_agent_id,
            "target_agent_id": self.target_agent_id,
            "critique": self.critique,
            "round_number": self.round_number,
            "agreement": self.agreement,
            "error": self.error,
        }


@dataclass
class DebateResult:
    """辩论最终结果。"""

    question: str
    verdict: str
    verdict_confidence: float
    verdict_agent: str | None = None
    proposals: list[DebateProposal] = field(default_factory=list)
    critiques: list[DebateCritique] = field(default_factory=list)
    num_rounds_completed: int = 0
    execution_time_ms: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "verdict": self.verdict,
            "verdict_confidence": self.verdict_confidence,
            "verdict_agent": self.verdict_agent,
            "proposals": [p.to_dict() for p in self.proposals],
            "critiques": [c.to_dict() for c in self.critiques],
            "num_rounds_completed": self.num_rounds_completed,
            "execution_time_ms": self.execution_time_ms,
            "trace": self.trace,
            "error": self.error,
        }
