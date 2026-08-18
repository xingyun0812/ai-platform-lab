#!/usr/bin/env python3
"""Memory Governance 配置和指标单元测试 — Phase X

运行：
    python -m pytest tests/test_memory_governance_config.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.memory.config import MemoryGovernanceConfig  # noqa: E402
from packages.memory.metrics import (  # noqa: E402
    get_memory_metrics,
    reset_metrics_for_tests,
)


def setup_method() -> None:
    reset_metrics_for_tests()


def test_config_defaults() -> None:
    """所有新字段有正确的默认值。"""
    cfg = MemoryGovernanceConfig()
    assert cfg.quality_filter_enabled is True
    assert cfg.min_content_length == 20
    # Dedup
    assert cfg.dedup_enabled is True
    assert cfg.dedup_skip_threshold == 0.92
    assert cfg.dedup_merge_threshold == 0.85
    assert cfg.dedup_candidate_count == 20
    assert cfg.dedup_merge_with_llm is False
    # Verify
    assert cfg.verify_enabled is True
    assert cfg.verify_model is None
    assert cfg.verify_confidence_threshold == 0.6
    assert cfg.verify_demote_threshold == 0.3
    # Weight
    assert cfg.weight_decay_enabled is True
    assert cfg.decay_lambda == 0.1
    assert cfg.recency_weight == 0.4
    assert cfg.frequency_weight == 0.3
    assert cfg.relevance_weight == 0.2
    assert cfg.feedback_weight == 0.1
    # Purge
    assert cfg.purge_enabled is True
    assert cfg.purge_min_weight == 0.1
    assert cfg.purge_zero_access_days == 30
    assert cfg.purge_low_weight_days == 90
    assert cfg.archive_enabled is True
    assert cfg.archive_retention_days == 365
    assert cfg.governance_cron == "0 3 * * *"
    print("PASS test_config_defaults")


def test_config_custom_values() -> None:
    """传参覆盖默认值。"""
    cfg = MemoryGovernanceConfig(
        dedup_enabled=False,
        dedup_skip_threshold=0.99,
        verify_enabled=False,
        weight_decay_enabled=False,
        purge_enabled=False,
        governance_cron="0 6 * * *",
    )
    assert cfg.dedup_enabled is False
    assert cfg.dedup_skip_threshold == 0.99
    assert cfg.verify_enabled is False
    assert cfg.weight_decay_enabled is False
    assert cfg.purge_enabled is False
    assert cfg.governance_cron == "0 6 * * *"
    print("PASS test_config_custom_values")


def test_config_backward_compatible() -> None:
    """只传旧字段，新字段用默认值。"""
    cfg = MemoryGovernanceConfig(
        quality_filter_enabled=False,
        min_content_length=5,
        dedup_skip_threshold=0.95,
        recency_weight=0.5,
    )
    assert cfg.quality_filter_enabled is False
    assert cfg.min_content_length == 5
    assert cfg.dedup_skip_threshold == 0.95
    assert cfg.recency_weight == 0.5
    # New fields still have defaults
    assert cfg.dedup_enabled is True
    assert cfg.verify_enabled is True
    assert cfg.purge_enabled is True
    print("PASS test_config_backward_compatible")


def test_metrics_dedup_skipped() -> None:
    """record_dedup_skipped 递增计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_dedup_skipped(tenant_id="t1", scope="user")
    m.record_dedup_skipped(tenant_id="t1", scope="user")
    m.record_dedup_skipped(tenant_id="t1", scope="tenant")
    text = m.prometheus_text()
    lines = [ln for ln in text.splitlines() if "memory_dedup_skipped_total{" in ln]
    assert len(lines) == 2
    assert any('scope="user"' in ln for ln in lines)
    assert any('scope="tenant"' in ln for ln in lines)
    # user 计数 2
    user_line = [ln for ln in lines if 'scope="user"' in ln][0]
    assert " 2" in user_line
    print("PASS test_metrics_dedup_skipped")


def test_metrics_dedup_merged() -> None:
    """record_dedup_merged 递增计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_dedup_merged(tenant_id="t1", scope="user")
    text = m.prometheus_text()
    assert 'memory_dedup_merged_total{tenant_id="t1",scope="user"} 1' in text
    print("PASS test_metrics_dedup_merged")


def test_metrics_verify_check() -> None:
    """record_verify_check 递增计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_verify_check(tenant_id="t1", scope="session")
    text = m.prometheus_text()
    assert 'memory_verify_check_total{tenant_id="t1",scope="session"} 1' in text
    print("PASS test_metrics_verify_check")


def test_metrics_verify_demoted() -> None:
    """record_verify_demoted 递增计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_verify_demoted(tenant_id="t1", scope="user")
    m.record_verify_demoted(tenant_id="t1", scope="user")
    text = m.prometheus_text()
    assert 'memory_verify_demoted_total{tenant_id="t1",scope="user"} 2' in text
    print("PASS test_metrics_verify_demoted")


def test_metrics_verify_latency() -> None:
    """record_verify_latency 记录值正确。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_verify_latency(tenant_id="t1", scope="user", latency_ms=150.0)
    m.record_verify_latency(tenant_id="t1", scope="user", latency_ms=250.0)
    text = m.prometheus_text()
    assert "memory_verify_latency_ms" in text
    print("PASS test_metrics_verify_latency")


def test_metrics_purge() -> None:
    """record_purge 按 reason 计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_purge(reason="expired")
    m.record_purge(reason="expired")
    m.record_purge(reason="low_weight")
    text = m.prometheus_text()
    assert 'governance_purge_total{reason="expired"} 2' in text
    assert 'governance_purge_total{reason="low_weight"} 1' in text
    print("PASS test_metrics_purge")


def test_metrics_archive() -> None:
    """record_archive 递增计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_archive()
    m.record_archive()
    m.record_archive()
    text = m.prometheus_text()
    assert "governance_archived_total 3" in text
    print("PASS test_metrics_archive")


def test_metrics_governance_run() -> None:
    """record_governance_run 记录 duration。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_governance_run(duration_seconds=12.5)
    text = m.prometheus_text()
    assert "governance_runtime_seconds 12.5" in text
    print("PASS test_metrics_governance_run")


def test_metrics_library_stats() -> None:
    """record_library_total / expired 按 scope 计数。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_library_total(tenant_id="t1", scope="user", count=42)
    m.record_library_expired(tenant_id="t1", scope="user", count=3)
    text = m.prometheus_text()
    assert 'memory_library_total{tenant_id="t1",scope="user"} 42' in text
    assert 'memory_library_expired{tenant_id="t1",scope="user"} 3' in text
    print("PASS test_metrics_library_stats")


def test_prometheus_has_all_new_metrics() -> None:
    """prometheus_text 包含所有 10 个新指标。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    # 写入至少一条数据到每个指标
    m.record_dedup_skipped(tenant_id="t1", scope="user")
    m.record_dedup_merged(tenant_id="t1", scope="user")
    m.record_verify_check(tenant_id="t1", scope="user")
    m.record_verify_demoted(tenant_id="t1", scope="user")
    m.record_verify_latency(tenant_id="t1", scope="user", latency_ms=100.0)
    m.record_purge(reason="test")
    m.record_archive()
    m.record_governance_run(duration_seconds=1.0)
    m.record_library_total(tenant_id="t1", scope="user", count=10)
    m.record_library_expired(tenant_id="t1", scope="user", count=1)
    text = m.prometheus_text()
    expected = [
        "memory_dedup_skipped_total",
        "memory_dedup_merged_total",
        "memory_verify_check_total",
        "memory_verify_demoted_total",
        "memory_verify_latency_ms",
        "governance_purge_total",
        "governance_archived_total",
        "governance_runtime_seconds",
        "memory_library_total",
        "memory_library_expired",
    ]
    for name in expected:
        assert name in text, f"指标 {name} 不在 prometheus_text 输出中"
    print("PASS test_prometheus_has_all_new_metrics")


def test_prometheus_still_has_old_metrics() -> None:
    """prometheus_text 仍包含旧指标。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    m.record_add(tenant_id="t1", scope="user")
    m.record_search(tenant_id="t1", scope="user")
    text = m.prometheus_text()
    assert "memory_adds_total" in text
    assert "memory_searches_total" in text
    assert "memory_search_latency_ms_p95" in text
    print("PASS test_prometheus_still_has_old_metrics")


def test_metrics_lock_safety() -> None:
    """并发调用 metrics 方法不抛异常。"""
    reset_metrics_for_tests()
    m = get_memory_metrics()
    import random
    import threading

    def worker() -> None:
        for _ in range(20):
            tid = f"t{random.randint(1, 3)}"
            m.record_dedup_skipped(tenant_id=tid, scope="user")
            m.record_verify_check(tenant_id=tid, scope="user")
            m.record_purge(reason="expired")

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = m.prometheus_text()
    assert "memory_dedup_skipped_total" in text
    assert "governance_purge_total" in text
    print("PASS test_metrics_lock_safety")


def main() -> int:
    tests = [
        test_config_defaults,
        test_config_custom_values,
        test_config_backward_compatible,
        test_metrics_dedup_skipped,
        test_metrics_dedup_merged,
        test_metrics_verify_check,
        test_metrics_verify_demoted,
        test_metrics_verify_latency,
        test_metrics_purge,
        test_metrics_archive,
        test_metrics_governance_run,
        test_metrics_library_stats,
        test_prometheus_has_all_new_metrics,
        test_prometheus_still_has_old_metrics,
        test_metrics_lock_safety,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
