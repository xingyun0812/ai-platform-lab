"""L5 Weighted Scoring — search-time weight computation.

Computes a composite weight for a MemoryRecord at search time using:

    w = recency_score * alpha + frequency_score * beta
        + relevance_score * gamma + feedback_score * delta

Weight is NOT stored — it is computed on the fly from access_count,
last_accessed_at, and feedback_bonus in metadata.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packages.memory.config import MemoryGovernanceConfig

if TYPE_CHECKING:
    from packages.memory.store import MemoryRecord


@dataclass
class ScopeStats:
    """Per-scope statistics used for frequency-score normalization."""

    max_access_count: int = 0


def compute_scope_stats(records: list[MemoryRecord]) -> ScopeStats:
    """Compute ScopeStats from a list of records.

    Determines the maximum access_count across the scope so that
    frequency_score can be normalized.
    """
    if not records:
        return ScopeStats()
    max_count = max(r.access_count for r in records)
    return ScopeStats(max_access_count=max_count)


def compute_weight(
    record: MemoryRecord,
    scope_stats: ScopeStats | None,
    config: MemoryGovernanceConfig,
) -> float:
    """Compute the L5 weighted score for a single record at search time.

    Formula:
        w = recency_score * alpha
            + frequency_score * beta
            + relevance_score * gamma
            + feedback_score * delta

    When ``weight_decay_enabled`` is False the function returns the
    record's raw ``weight`` field value (legacy behavior).

    Args:
        record: The memory record to score.
        scope_stats: Scope-level statistics (used for frequency
            normalization).  When ``None`` or when the scope is empty,
            frequency_score falls back to ``min(1.0, log(1 + access_count))``.
        config: Governance configuration carrying the weight coefficients
            and decay settings.

    Returns:
        A float weight in the range ``[-1 * delta, 1.0]``
        (the feedback term can pull the score slightly negative).
    """
    # -- Legacy bypass -------------------------------------------------------
    if not config.weight_decay_enabled:
        return record.weight

    alpha = config.recency_weight
    beta = config.frequency_weight
    gamma = config.relevance_weight
    delta = config.feedback_weight

    # -- Recency score -------------------------------------------------------
    # Use last_accessed_at; fall back to created_at if never accessed.
    ref_time = record.last_accessed_at or record.created_at
    days_since = (time.time() - ref_time) / 86400.0
    recency_score = math.exp(-config.decay_lambda * days_since)

    # -- Frequency score -----------------------------------------------------
    log_access = math.log1p(record.access_count)
    if scope_stats is not None and scope_stats.max_access_count > 0:
        log_max = math.log1p(scope_stats.max_access_count)
        frequency_score = min(1.0, log_access / log_max)
    else:
        # When no scope stats are available, use the access count directly.
        frequency_score = min(1.0, log_access)

    # -- Relevance score (placeholder) --------------------------------------
    relevance_score = 1.0

    # -- Feedback score ------------------------------------------------------
    feedback_bonus = record.metadata.get("feedback_bonus", 0.0)
    if not isinstance(feedback_bonus, (int, float)):
        feedback_bonus = 0.0
    feedback_score = max(-1.0, min(1.0, float(feedback_bonus)))

    # -- Composite -----------------------------------------------------------
    return (
        recency_score * alpha
        + frequency_score * beta
        + relevance_score * gamma
        + feedback_score * delta
    )
