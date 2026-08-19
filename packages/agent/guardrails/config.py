"""AgentGuardrailConfig — 统一配置模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentGuardrailConfig:
    """Agent 执行防护统一配置。

    所有四层防护的参数集中管理。可通过 settings.py 设置全局默认值，
    或通过 agent.yaml 按 Agent 类型覆盖。
    """

    # Layer 1: Granularity
    plan_max_steps: int = 20
    plan_max_depth: int = 3
    plan_min_step_description: int = 10

    # Layer 2: Convergence
    convergence_enabled: bool = True
    convergence_strategy: str = "hybrid"  # "similarity" | "llm_judged" | "hybrid"
    convergence_threshold: float = 0.85
    convergence_no_tool_rounds: int = 2  # 连续 N 轮无 tool_call 且输出稳定则收敛

    # Layer 3: Progress
    progress_check_enabled: bool = True
    max_consecutive_empty_tools: int = 3
    max_consecutive_identical_calls: int = 3

    # Layer 4: Thresholds
    max_tool_calls_total: int = 30
    tool_call_limits: dict[str, int] = field(
        default_factory=lambda: {
            "web_search": 5,
            "sql_query": 10,
            "computer_use": 20,
        }
    )
    agent_timeout_seconds: float = 300.0

    # Global switch
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_max_steps": self.plan_max_steps,
            "plan_max_depth": self.plan_max_depth,
            "plan_min_step_description": self.plan_min_step_description,
            "convergence_enabled": self.convergence_enabled,
            "convergence_strategy": self.convergence_strategy,
            "convergence_threshold": self.convergence_threshold,
            "convergence_no_tool_rounds": self.convergence_no_tool_rounds,
            "progress_check_enabled": self.progress_check_enabled,
            "max_consecutive_empty_tools": self.max_consecutive_empty_tools,
            "max_consecutive_identical_calls": self.max_consecutive_identical_calls,
            "max_tool_calls_total": self.max_tool_calls_total,
            "tool_call_limits": dict(self.tool_call_limits),
            "agent_timeout_seconds": self.agent_timeout_seconds,
            "enabled": self.enabled,
        }
