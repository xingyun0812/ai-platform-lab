#!/usr/bin/env python3
"""质量监控单元测试 — Phase J #48

运行：
    python3 tests/test_quality_monitor.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_agg_mod = _load("packages/quality_monitor/aggregator.py", "packages.quality_monitor.aggregator")
_alert_mod = _load("packages/quality_monitor/alerts.py", "packages.quality_monitor.alerts")

QualityMetric = _agg_mod.QualityMetric
QualityAggregator = _agg_mod.QualityAggregator
init_quality_monitor = _agg_mod.init_quality_monitor
get_quality_monitor = _agg_mod.get_quality_monitor
reset_for_tests = _agg_mod.reset_for_tests

QualityAlert = _alert_mod.QualityAlert
AlertChecker = _alert_mod.AlertChecker


def _run_async(coro):
    return asyncio.run(coro)


# ─────────────────────────── Tests ───────────────────────────


def test_quality_metric_dataclass():
    m = QualityMetric(
        tenant_id="t1",
        window_seconds=300,
        total_requests=100,
        thumbs_up=80,
        thumbs_down=20,
        avg_rating=4.2,
        bad_case_count=5,
        satisfaction_rate=0.8,
    )
    assert m.tenant_id == "t1"
    assert m.satisfaction_rate == 0.8
    assert m.avg_rating == 4.2
    assert isinstance(m.timestamp, float)
    print("PASS test_quality_metric_dataclass")


def test_aggregator_record_and_get_current():
    reset_for_tests()
    agg = QualityAggregator(window_seconds=300)

    async def run():
        await agg.record_request("t1", "thumbs_up")
        await agg.record_request("t1", "thumbs_up")
        await agg.record_request("t1", "thumbs_down")
        metric = await agg.get_current("t1", window_seconds=300)
        assert metric.total_requests == 3
        assert metric.thumbs_up == 2
        assert metric.thumbs_down == 1
        assert round(metric.satisfaction_rate, 4) == round(2 / 3, 4)

    _run_async(run())
    print("PASS test_aggregator_record_and_get_current")


def test_aggregator_empty_tenant():
    reset_for_tests()
    agg = QualityAggregator(window_seconds=300)

    async def run():
        metric = await agg.get_current("nonexistent-tenant")
        assert metric.total_requests == 0
        # 无数据时满意度默认 1.0
        assert metric.satisfaction_rate == 1.0

    _run_async(run())
    print("PASS test_aggregator_empty_tenant")


def test_aggregator_rating():
    reset_for_tests()
    agg = QualityAggregator(window_seconds=300)

    async def run():
        await agg.record_request("t1", "rating_4", rating=4)
        await agg.record_request("t1", "rating_2", rating=2)
        await agg.record_request("t1", "rating_5", rating=5)
        metric = await agg.get_current("t1")
        assert abs(metric.avg_rating - (4 + 2 + 5) / 3) < 0.01

    _run_async(run())
    print("PASS test_aggregator_rating")


def test_aggregator_bad_case_count():
    reset_for_tests()
    agg = QualityAggregator(window_seconds=300)

    async def run():
        await agg.record_request("t1", "thumbs_down")
        await agg.record_request("t1", "bad_case")
        await agg.record_request("t1", "rating_1")
        await agg.record_request("t1", "thumbs_up")
        metric = await agg.get_current("t1")
        assert metric.bad_case_count == 3

    _run_async(run())
    print("PASS test_aggregator_bad_case_count")


def test_aggregator_get_trend():
    reset_for_tests()
    agg = QualityAggregator(window_seconds=60)

    async def run():
        await agg.record_request("t1", "thumbs_up")
        trend = await agg.get_trend("t1", windows=5)
        assert len(trend) == 5
        # 最后一个窗口应包含刚记录的事件
        last = trend[-1]
        assert isinstance(last, QualityMetric)

    _run_async(run())
    print("PASS test_aggregator_get_trend")


def test_singleton_init_get():
    reset_for_tests()
    assert get_quality_monitor() is None
    agg = init_quality_monitor()
    assert agg is not None
    assert get_quality_monitor() is agg
    reset_for_tests()
    assert get_quality_monitor() is None
    print("PASS test_singleton_init_get")


def test_alert_checker_satisfaction_drop():
    checker = AlertChecker()
    # 满意度低于阈值 → 产生告警
    m = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=50, thumbs_down=50,
        avg_rating=3.0, bad_case_count=5, satisfaction_rate=0.5,
    )
    alert = checker.check_satisfaction_drop(m, threshold=0.7)
    assert alert is not None
    assert alert.alert_type == "satisfaction_drop"
    assert alert.severity in ("warning", "critical")
    assert alert.current_value == 0.5

    # 满意度正常 → 无告警
    m2 = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=90, thumbs_down=10,
        avg_rating=4.5, bad_case_count=2, satisfaction_rate=0.9,
    )
    assert checker.check_satisfaction_drop(m2, threshold=0.7) is None
    print("PASS test_alert_checker_satisfaction_drop")


def test_alert_checker_bad_case_spike():
    checker = AlertChecker()
    m = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=200, thumbs_up=150, thumbs_down=15,
        avg_rating=3.5, bad_case_count=25, satisfaction_rate=0.75,
    )
    alert = checker.check_bad_case_spike(m, threshold=10)
    assert alert is not None
    assert alert.alert_type == "bad_case_spike"
    assert alert.current_value == 25

    m2 = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=95, thumbs_down=5,
        avg_rating=4.5, bad_case_count=3, satisfaction_rate=0.95,
    )
    assert checker.check_bad_case_spike(m2, threshold=10) is None
    print("PASS test_alert_checker_bad_case_spike")


def test_alert_checker_rating_decline():
    checker = AlertChecker()
    prev = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=80, thumbs_down=20,
        avg_rating=4.5, bad_case_count=3, satisfaction_rate=0.8,
    )
    curr = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=60, thumbs_down=40,
        avg_rating=2.0, bad_case_count=10, satisfaction_rate=0.6,
    )
    alert = checker.check_rating_decline(curr, prev, threshold=0.5)
    assert alert is not None
    assert alert.alert_type == "rating_decline"
    assert alert.current_value == 2.0

    # 无下降
    prev2 = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=80, thumbs_down=20,
        avg_rating=4.0, bad_case_count=3, satisfaction_rate=0.8,
    )
    curr2 = QualityMetric(
        tenant_id="t1", window_seconds=300,
        total_requests=100, thumbs_up=85, thumbs_down=15,
        avg_rating=4.2, bad_case_count=2, satisfaction_rate=0.85,
    )
    assert checker.check_rating_decline(curr2, prev2, threshold=0.5) is None
    print("PASS test_alert_checker_rating_decline")


def test_run_all_checks_no_aggregator():
    """无 aggregator 时 run_all_checks 返回空列表（不崩溃）"""
    reset_for_tests()
    checker = AlertChecker()

    async def run():
        alerts = await checker.run_all_checks("t1")
        assert alerts == []

    _run_async(run())
    print("PASS test_run_all_checks_no_aggregator")


# ─────────────────────────── Main ────────────────────────────

if __name__ == "__main__":
    tests = [
        test_quality_metric_dataclass,
        test_aggregator_record_and_get_current,
        test_aggregator_empty_tenant,
        test_aggregator_rating,
        test_aggregator_bad_case_count,
        test_aggregator_get_trend,
        test_singleton_init_get,
        test_alert_checker_satisfaction_drop,
        test_alert_checker_bad_case_spike,
        test_alert_checker_rating_decline,
        test_run_all_checks_no_aggregator,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
