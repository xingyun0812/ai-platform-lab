"""Memory Governance 配置。

MemoryGovernanceConfig 集中管理 quality_filter、dedup_filter 等治理规则的阈值和开关。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryGovernanceConfig:
    """记忆治理配置。"""

    # quality_filter 规则
    quality_filter_enabled: bool = True
    min_content_length: int = 20

    # dedup_filter 阈值（PRD-1 L2 — 预留，当前未使用）
    dedup_skip_threshold: float = 0.95
    dedup_merge_threshold: float = 0.85

    # rerank 开关（PRD-3 — 预留，当前未使用）
    rerank_enabled: bool = True

    # 权重衰减配置（PRD-2 — 预留，当前未使用）
    recency_weight: float = 0.4
    frequency_weight: float = 0.3
    relevance_weight: float = 0.2
    feedback_weight: float = 0.1
