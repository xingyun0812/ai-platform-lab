"""Tests for L5 Weighted Scoring on Search (Issue #218)."""

from __future__ import annotations

import math
import time

import pytest

from packages.memory.config import MemoryGovernanceConfig
from packages.memory.governance.weight import (
    ScopeStats,
    compute_scope_stats,
    compute_weight,
)
from packages.memory.store import MemoryRecord

# -- Helpers ----------------------------------------------------------------


def _record(
    access_count: int = 0,
    last_accessed_at: float | None = None,
    feedback_bonus: float = 0.0,
    created_at: float | None = None,
) -> MemoryRecord:
    """Create a minimal MemoryRecord for weight tests."""
    meta = {"feedback_bonus": feedback_bonus} if feedback_bonus else {}
    return MemoryRecord(
        memory_id="test-mem-1",
        tenant_id="t1",
        scope="user",
        scope_id="u1",
        content="hello world",
        created_at=created_at or time.time(),
        access_count=access_count,
        last_accessed_at=last_accessed_at,
        metadata=meta,
    )


_DEFAULT_CONFIG = MemoryGovernanceConfig()


# -- ScopeStats tests -------------------------------------------------------


def test_scope_stats_empty():
    """ScopeStats with zero records returns defaults."""
    stats = compute_scope_stats([])
    assert stats.max_access_count == 0


def test_scope_stats_single():
    stats = compute_scope_stats([_record(access_count=5)])
    assert stats.max_access_count == 5


def test_scope_stats_max():
    stats = compute_scope_stats(
        [_record(access_count=3), _record(access_count=10), _record(access_count=1)]
    )
    assert stats.max_access_count == 10


# -- compute_weight tests ---------------------------------------------------


def test_all_weights_zero():
    """All weights 0 -> returns 0."""
    cfg = MemoryGovernanceConfig(
        recency_weight=0.0,
        frequency_weight=0.0,
        relevance_weight=0.0,
        feedback_weight=0.0,
    )
    r = _record(access_count=10, last_accessed_at=time.time())
    w = compute_weight(r, None, cfg)
    assert w == 0.0


def test_weight_decay_disabled_returns_raw_weight():
    """weight_decay_enabled=False -> returns the record's raw weight field."""
    cfg = MemoryGovernanceConfig(weight_decay_enabled=False)
    r = _record(access_count=999, last_accessed_at=time.time() - 99999)
    # Force the raw weight field to a known value
    r.weight = 0.42
    w = compute_weight(r, None, cfg)
    assert w == 0.42


def test_recency_recent_scores_higher():
    """Recently accessed record scores higher than stale one."""
    now = time.time()
    recent = _record(access_count=0, last_accessed_at=now)
    stale = _record(access_count=0, last_accessed_at=now - 365 * 86400)  # 1 year ago
    # No scope stats -> frequency_score is 0 (log1p(0)=0)
    stats = ScopeStats(max_access_count=0)
    w_recent = compute_weight(recent, stats, _DEFAULT_CONFIG)
    w_stale = compute_weight(stale, stats, _DEFAULT_CONFIG)
    assert w_recent > w_stale, f"recent={w_recent:.4f} should be > stale={w_stale:.4f}"


def test_frequency_high_scores_higher():
    """High-frequency record scores higher than low-frequency."""
    now = time.time()
    high = _record(access_count=50, last_accessed_at=now)
    low = _record(access_count=1, last_accessed_at=now)
    stats = ScopeStats(max_access_count=50)
    w_high = compute_weight(high, stats, _DEFAULT_CONFIG)
    w_low = compute_weight(low, stats, _DEFAULT_CONFIG)
    assert w_high > w_low, f"high={w_high:.4f} should be > low={w_low:.4f}"


def test_recency_only_weight():
    """recency_weight=1.0, others=0.0 -> sort purely by recency."""
    cfg = MemoryGovernanceConfig(
        recency_weight=1.0,
        frequency_weight=0.0,
        relevance_weight=0.0,
        feedback_weight=0.0,
    )
    now = time.time()
    recent = _record(access_count=0, last_accessed_at=now)
    stale = _record(access_count=0, last_accessed_at=now - 365 * 86400)
    w_recent = compute_weight(recent, None, cfg)
    w_stale = compute_weight(stale, None, cfg)
    assert w_recent == pytest.approx(
        1.0, rel=1e-9
    )  # accessed "now" means days_since=0 -> e^0 = 1.0
    assert w_stale < 1.0


def test_feedback_bonus_boosts_score():
    """feedback_bonus=0.5 boosts the score."""
    now = time.time()
    r_with_feedback = _record(access_count=0, last_accessed_at=now, feedback_bonus=0.5)
    r_no_feedback = _record(access_count=0, last_accessed_at=now, feedback_bonus=0.0)
    w_with = compute_weight(r_with_feedback, None, _DEFAULT_CONFIG)
    w_without = compute_weight(r_no_feedback, None, _DEFAULT_CONFIG)
    assert w_with > w_without, f"with_feedback={w_with:.4f} should be > without={w_without:.4f}"


def test_feedback_bonus_clamped():
    """feedback_bonus is clamped to [-1.0, 1.0]."""
    cfg = MemoryGovernanceConfig(
        feedback_weight=1.0, recency_weight=0.0, frequency_weight=0.0, relevance_weight=0.0
    )
    r_over = _record(access_count=0, last_accessed_at=time.time(), feedback_bonus=100.0)
    r_under = _record(access_count=0, last_accessed_at=time.time(), feedback_bonus=-100.0)
    w_over = compute_weight(r_over, None, cfg)
    w_under = compute_weight(r_under, None, cfg)
    assert w_over == 1.0, f"clamped 100 -> {w_over}"
    assert w_under == -1.0, f"clamped -100 -> {w_under}"


def test_never_accessed():
    """Record with access_count=0, last_accessed_at=None uses created_at."""
    now = time.time()
    cfg = MemoryGovernanceConfig(
        recency_weight=1.0, frequency_weight=0.0, relevance_weight=0.0, feedback_weight=0.0
    )
    r = _record(access_count=0, last_accessed_at=None, created_at=now)
    w = compute_weight(r, None, cfg)
    assert w == pytest.approx(1.0, rel=1e-9), f"should be 1.0 for created_at=now, got {w:.4f}"


def test_decay_lambda_zero():
    """decay_lambda=0 -> no decay, recency_score always 1.0."""
    cfg = MemoryGovernanceConfig(
        decay_lambda=0.0,
        recency_weight=1.0,
        frequency_weight=0.0,
        relevance_weight=0.0,
        feedback_weight=0.0,
    )
    stale = _record(access_count=0, last_accessed_at=time.time() - 1000 * 86400)
    w = compute_weight(stale, None, cfg)
    assert w == 1.0, f"lambda=0 should give 1.0, got {w:.4f}"


def test_scope_stats_none_fallback():
    """When scope_stats is None, frequency_score uses direct log normalization."""
    cfg = MemoryGovernanceConfig(
        recency_weight=0.0, frequency_weight=1.0, relevance_weight=0.0, feedback_weight=0.0
    )
    r = _record(access_count=10, last_accessed_at=time.time())
    w = compute_weight(r, None, cfg)
    expected = min(1.0, math.log1p(10))
    assert w == expected, f"expected {expected:.4f}, got {w:.4f}"
