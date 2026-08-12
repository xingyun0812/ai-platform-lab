from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SelfRefineConfig:
    """Self-Refine 策略配置。

    Self-Refine（Madaan et al., 2023）让 LLM 自我生成 → 自我反馈 → 自我修正，
    迭代收敛至最优输出。

    Attributes:
        max_iterations: 最大迭代轮数（上限 10）。
        generator_model: 生成器模型，可分离。None 时使用 settings 默认模型。
        feedback_model: 反馈器模型，可分离。None 时复用 generator_model。
        convergence_strategy: 收敛判断策略。
            llm_judged — LLM 判断是否有新改进点。
            similarity — 语义相似度 > threshold 即收敛。
            hybrid — sequential AND：similarity >= threshold 跳过 LLM judge，
                     否则 LLM judge 确认。
        convergence_threshold: similarity 模式阈值（0.0 ~ 1.0）。
        max_total_llm_calls: 单次请求硬上限 LLM 调用次数（默认 15，上限 30）。
        feedback_dimensions: 结构化反馈维度列表。None 表示自由文本模式。
        temperature: LLM 生成温度。
        timeout_seconds: 超时截断时间。
    """

    enabled: bool = True
    max_iterations: int = 5
    generator_model: str | None = None
    feedback_model: str | None = None
    convergence_strategy: str = "hybrid"  # llm_judged | similarity | hybrid
    convergence_threshold: float = 0.85
    max_total_llm_calls: int = 15
    feedback_dimensions: tuple[str, ...] = (
        "correctness",
        "clarity",
        "completeness",
        "consistency",
        "actionability",
    )
    temperature: float = 0.3
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_iterations < 1 or self.max_iterations > 10:
            raise ValueError(f"max_iterations must be 1-10, got {self.max_iterations}")
        if self.max_total_llm_calls < 1 or self.max_total_llm_calls > 30:
            raise ValueError(f"max_total_llm_calls must be 1-30, got {self.max_total_llm_calls}")
        if self.convergence_strategy not in ("llm_judged", "similarity", "hybrid"):
            raise ValueError(f"unknown convergence_strategy: {self.convergence_strategy}")
        if not 0.0 <= self.convergence_threshold <= 1.0:
            raise ValueError(f"convergence_threshold must be 0-1, got {self.convergence_threshold}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_iterations": self.max_iterations,
            "generator_model": self.generator_model,
            "feedback_model": self.feedback_model,
            "convergence_strategy": self.convergence_strategy,
            "convergence_threshold": self.convergence_threshold,
            "max_total_llm_calls": self.max_total_llm_calls,
            "feedback_dimensions": list(self.feedback_dimensions),
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
        }
