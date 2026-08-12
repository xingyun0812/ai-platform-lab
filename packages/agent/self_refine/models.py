from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.agent.self_refine.config import SelfRefineConfig


@dataclass
class FeedbackRound:
    """单轮 Self-Refine 反馈记录。"""

    iteration: int
    feedback: str
    feedback_dimension: str | None  # structured dimension or None for free-text
    feedback_error: str | None = None
    refine_error: str | None = None
    output_after_refine: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "feedback": self.feedback,
            "feedback_dimension": self.feedback_dimension,
            "feedback_error": self.feedback_error,
            "refine_error": self.refine_error,
            "output_after_refine": self.output_after_refine,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class SelfRefineResult:
    """Self-Refine 最终结果。

    Attributes:
        prompt: 原始 prompt。
        final_output: 最终输出。
        config: 使用的配置。
        iterations_completed: 实际完成的迭代次数。
        converged: 是否收敛。
        convergence_reason: 收敛原因（llm_judged | similarity | max_iterations | max_calls | timeout）。
        trace: 每轮反馈记录。
        execution_time_ms: 总耗时。
        total_llm_calls: 实际 LLM 调用次数。
        error: 错误信息（如有）。
        success: 是否成功完成（非错误终止）。
    """

    prompt: str
    final_output: str
    config: SelfRefineConfig
    iterations_completed: int = 0
    converged: bool = False
    convergence_reason: str = ""
    trace: list[FeedbackRound] = field(default_factory=list)
    execution_time_ms: float = 0.0
    total_llm_calls: int = 0
    error: str | None = None
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "final_output": self.final_output,
            "config": self.config.to_dict(),
            "iterations_completed": self.iterations_completed,
            "converged": self.converged,
            "convergence_reason": self.convergence_reason,
            "trace": [t.to_dict() for t in self.trace],
            "execution_time_ms": self.execution_time_ms,
            "total_llm_calls": self.total_llm_calls,
            "error": self.error,
            "success": self.success,
        }
