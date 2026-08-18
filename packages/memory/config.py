"""Memory Governance 配置。

MemoryGovernanceConfig 集中管理 quality_filter、dedup_filter 等治理规则的阈值和开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryGovernanceConfig:
    """记忆治理配置。

    集中管理 quality_filter、dedup、rerank、权重衰减、召回校验、清理归档等
    治理规则的阈值和开关。
    """

    # — L1: quality_filter 规则 —
    quality_filter_enabled: bool = True
    min_content_length: int = 20

    # — L2: Dedup 阈值 —
    dedup_enabled: bool = True
    dedup_skip_threshold: float = 0.92
    dedup_merge_threshold: float = 0.85
    dedup_candidate_count: int = 20
    dedup_merge_with_llm: bool = False

    # — L4: Recall Verification —
    rerank_enabled: bool = True  # 兼容旧名称，实际是 verify_enabled 的别名
    verify_enabled: bool = True
    verify_model: str | None = None
    verify_confidence_threshold: float = 0.6
    verify_demote_threshold: float = 0.3

    # — L5: 权重衰减 —
    weight_decay_enabled: bool = True
    decay_lambda: float = 0.1
    recency_weight: float = 0.4
    frequency_weight: float = 0.3
    relevance_weight: float = 0.2
    feedback_weight: float = 0.1

    # — L3: Purge & Archive —
    purge_enabled: bool = True
    purge_min_weight: float = 0.1
    purge_zero_access_days: int = 30
    purge_low_weight_days: int = 90
    archive_enabled: bool = True
    archive_retention_days: int = 365
    governance_cron: str = "0 3 * * *"

    # — Metadata —
    metadata: dict[str, Any] = field(default_factory=dict)
