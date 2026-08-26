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
        reflection_depth: 反思深度（full | light | off | legacy）。
            默认 legacy = 现状 pass-through（保留既有多轮迭代+收敛行为）。
            off  → 仅生成一次，跳过所有迭代（零反思 LLM）。
            light → 单轮即时校验（生成 + 一轮反馈/修正），低时延。
            full → 多轮迭代 + 收敛判停 + 三重兜底。
        small_model: 反思链路低成本小模型（None = 复用 generator_model）。
        confidence_gate_enabled: 是否启用置信度闸门（小模型低置信度升级大模型复核）。
        confidence_threshold: 置信度闸门阈值（0-1）。
        max_total_latency_s: 迭代累计时延硬超时（秒），超过即提前终止。
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
    # -- 反思成本治理（#256，全部向后兼容）--
    reflection_depth: str = "legacy"  # full | light | off | legacy
    small_model: str | None = None
    confidence_gate_enabled: bool = False
    confidence_threshold: float = 0.85
    max_total_latency_s: float = 120.0

    def __post_init__(self) -> None:
        if self.max_iterations < 1 or self.max_iterations > 10:
            raise ValueError(f"max_iterations must be 1-10, got {self.max_iterations}")
        if self.max_total_llm_calls < 1 or self.max_total_llm_calls > 30:
            raise ValueError(f"max_total_llm_calls must be 1-30, got {self.max_total_llm_calls}")
        if self.convergence_strategy not in ("llm_judged", "similarity", "hybrid"):
            raise ValueError(f"unknown convergence_strategy: {self.convergence_strategy}")
        if not 0.0 <= self.convergence_threshold <= 1.0:
            raise ValueError(f"convergence_threshold must be 0-1, got {self.convergence_threshold}")
        if self.reflection_depth not in ("full", "light", "off", "legacy"):
            raise ValueError(f"unknown reflection_depth: {self.reflection_depth}")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(f"confidence_threshold must be 0-1, got {self.confidence_threshold}")

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
            "reflection_depth": self.reflection_depth,
            "small_model": self.small_model,
            "confidence_gate_enabled": self.confidence_gate_enabled,
            "confidence_threshold": self.confidence_threshold,
            "max_total_latency_s": self.max_total_latency_s,
        }
