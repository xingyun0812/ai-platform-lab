"""reflection_metrics 成本/收益指标单测（token/时延/触发原因/收敛轮数/ROI）。"""

from __future__ import annotations

import math

import pytest

from packages.agent.reflection_metrics import (
    ReflectionMetricsStore,
    ReflectionUse,
    compute_roi,
    record_reflection_use,
)


def _make_store(cost_per_1k: float = 0.0002) -> ReflectionMetricsStore:
    return ReflectionMetricsStore(cost_per_1k_tokens=cost_per_1k)


def test_record_reflection_use_auto_costs() -> None:
    store = _make_store()
    use = record_reflection_use(
        store,
        reason="task_failure",
        depth="full",
        action="check",
        tokens=5000,
        latency_ms=120.5,
        rounds=4,
    )
    assert use.cost == pytest.approx(5000 / 1000 * 0.0002)
    # 默认 outcome 由 action 推断
    assert use.outcome == "check"
    assert store.count() == 1
    assert store.total_tokens() == 5000


def test_record_deduped_outcome() -> None:
    store = _make_store()
    use = record_reflection_use(
        store,
        reason="consecutive_failure",
        depth="light",
        action="skip",
        tokens=0,
        deduped=True,
    )
    assert use.outcome == "deduped"
    assert use.cost == 0.0


def test_record_escalated_outcome() -> None:
    store = _make_store()
    use = record_reflection_use(
        store,
        reason="distortion",
        depth="light",
        action="check",
        tokens=30,
        escalated=True,
    )
    assert use.outcome == "escalated"


def test_total_aggregates() -> None:
    store = _make_store()
    record_reflection_use(store, reason="a", depth="light", action="check", tokens=100)
    record_reflection_use(store, reason="b", depth="full", action="check", tokens=250)
    assert store.total_tokens() == 350
    assert store.count() == 2


def test_by_depth_and_by_reason() -> None:
    store = _make_store()
    record_reflection_use(
        store, reason="task_failure", depth="full", action="check", tokens=100, rounds=3
    )
    record_reflection_use(
        store, reason="task_failure", depth="full", action="check", tokens=100, rounds=5
    )
    record_reflection_use(
        store, reason="consecutive_failure", depth="light", action="check", tokens=10
    )

    by_depth = store.by_depth()
    assert by_depth["full"]["uses"] == 2
    assert by_depth["full"]["tokens"] == 200
    assert by_depth["full"]["avg_rounds"] == 4.0
    assert by_depth["light"]["uses"] == 1

    by_reason = store.by_reason()
    assert by_reason["task_failure"] == 2
    assert by_reason["consecutive_failure"] == 1


def test_summary_present() -> None:
    store = _make_store()
    record_reflection_use(store, reason="task_failure", depth="off", action="pass", tokens=0)
    summary = store.summary()
    assert summary["total_uses"] == 1
    assert summary["total_tokens"] == 0
    assert "by_depth" in summary
    assert "by_reason" in summary


def test_roundtrip_to_dict() -> None:
    store = _make_store()
    use = record_reflection_use(
        store, reason="task_failure", depth="light", action="check", tokens=40, rounds=1
    )
    d = use.to_dict()
    assert d["reason"] == "task_failure"
    assert d["depth"] == "light"
    assert d["tokens"] == 40
    assert "cost" in d


def test_store_record_kwargs() -> None:
    store = _make_store()
    store.record(ReflectionUse(reason="x", depth="light", action="check", tokens=5))
    store.record(reason="y", depth="off", action="pass")
    assert store.count() == 2


# ---------------------------------------------------------------------------
# ROI 聚合
# ---------------------------------------------------------------------------


def test_compute_roi_positive_when_benefit_exceeds_cost() -> None:
    store = _make_store()
    record_reflection_use(store, reason="task_failure", depth="full", action="check", tokens=1000)
    result = compute_roi(
        store,
        error_rate_before=0.20,
        error_rate_after=0.10,
        cost_per_error=50.0,
        rework_count_before=10,
        rework_count_after=4,
        cost_per_rework=5.0,
    )
    # cost_total = 1000/1000 * 0.0002 = 0.0002，benefit=5.0，rework_saved=30
    assert result.cost_total == pytest.approx(0.0002)
    assert result.benefit == 5.0
    assert result.rework_saved == 30.0
    assert result.roi_ratio > 0


def test_compute_roi_zero_cost_is_pure_gain() -> None:
    store = _make_store()
    record_reflection_use(store, reason="off", depth="off", action="pass", tokens=0)
    result = compute_roi(
        store,
        error_rate_before=0.5,
        error_rate_after=0.4,
        cost_per_error=10.0,
        rework_count_before=2,
        rework_count_after=1,
        cost_per_rework=10.0,
    )
    assert result.cost_total == 0.0
    assert result.roi_ratio == math.inf


def test_compute_roi_no_benefit_zero_cost_ratio_zero() -> None:
    store = _make_store()
    result = compute_roi(
        store,
        error_rate_before=0.1,
        error_rate_after=0.1,
        cost_per_error=10.0,
        rework_count_before=1,
        rework_count_after=1,
        cost_per_rework=10.0,
    )
    assert result.cost_total == 0.0
    assert result.roi_ratio == 0.0


def test_compute_roi_roi_dict() -> None:
    store = _make_store()
    record_reflection_use(store, reason="a", depth="light", action="check", tokens=1000)
    result = compute_roi(
        store,
        error_rate_before=0.2,
        error_rate_after=0.1,
        cost_per_error=10.0,
        rework_count_before=5,
        rework_count_after=2,
        cost_per_rework=5.0,
    )
    d = result.to_dict()
    assert "roi_ratio" in d
    assert "roi_pct" in d
