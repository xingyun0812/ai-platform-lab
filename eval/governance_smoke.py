#!/usr/bin/env python3
"""Phase X — Memory Governance 完整性烟雾测试。

独立运行，不依赖 Gateway 或 LLM Key。

运行：
    python eval/governance_smoke.py
"""

from __future__ import annotations

import sys


def _check(name: str, passed: bool, detail: str = "") -> str:
    icon = "PASS" if passed else "FAIL"
    msg = f"  [{icon}] {name}"
    if detail:
        msg += f" — {detail}"
    return msg


def run_governance_smoke() -> list[str]:
    """运行所有治理模块的 smoke 检查。"""
    results: list[str] = []
    results.append("\n=== Phase X — Memory Governance Smoke ===\n")

    # 1. MemoryGovernanceConfig 字段完整性
    try:
        from packages.memory.config import MemoryGovernanceConfig

        cfg = MemoryGovernanceConfig()
        fields = {
            "quality_filter_enabled",
            "min_content_length",
            "dedup_enabled",
            "dedup_skip_threshold",
            "dedup_merge_threshold",
            "dedup_candidate_count",
            "dedup_merge_with_llm",
            "verify_enabled",
            "verify_model",
            "verify_confidence_threshold",
            "verify_demote_threshold",
            "weight_decay_enabled",
            "decay_lambda",
            "recency_weight",
            "frequency_weight",
            "relevance_weight",
            "feedback_weight",
            "purge_enabled",
            "purge_min_weight",
            "purge_zero_access_days",
            "purge_low_weight_days",
            "archive_enabled",
            "archive_retention_days",
            "governance_cron",
        }
        existing = {f for f in dir(cfg) if not f.startswith("_")}
        missing = fields - existing
        ok = len(missing) == 0
        results.append(
            _check("Config: 字段完整", ok, f"missing={sorted(missing) if missing else 'none'}")
        )

        # 验证默认值
        results.append(
            _check(
                "Config: default dedup_skip",
                cfg.dedup_skip_threshold == 0.92,
                str(cfg.dedup_skip_threshold),
            )
        )
        results.append(
            _check(
                "Config: default verify_enabled",
                cfg.verify_enabled is True,
                str(cfg.verify_enabled),
            )
        )
        results.append(
            _check("Config: default decay_lambda", cfg.decay_lambda == 0.1, str(cfg.decay_lambda))
        )
        results.append(
            _check(
                "Config: default purge_enabled", cfg.purge_enabled is True, str(cfg.purge_enabled)
            )
        )
        results.append(
            _check(
                "Config: default governance_cron",
                cfg.governance_cron == "0 3 * * *",
                cfg.governance_cron,
            )
        )
    except Exception as e:
        results.append(_check("Config: 模块加载", False, str(e)))

    # 2. Dedup 模块
    try:
        from packages.memory.governance.dedup import DedupResult, check_dedup

        results.append(
            _check(
                "Dedup: DedupResult", DedupResult(action="test").action == "test", "action field OK"
            )
        )
        results.append(_check("Dedup: check_dedup callable", callable(check_dedup), ""))
    except Exception as e:
        results.append(_check("Dedup: 模块加载", False, str(e)))

    # 3. Weight 模块
    try:
        from packages.memory.governance.weight import ScopeStats, compute_weight

        results.append(
            _check(
                "Weight: ScopeStats",
                ScopeStats().max_access_count == 0,
                "max_access_count default 0 OK",
            )
        )
        results.append(_check("Weight: compute_weight callable", callable(compute_weight), ""))
    except Exception as e:
        results.append(_check("Weight: 模块加载", False, str(e)))

    # 4. Verify 模块
    try:
        from packages.memory.governance.verify import Verdict, VerifyResult, verify_top_k_sync

        results.append(
            _check(
                "Verify: Verdict",
                Verdict(relevant=True, confidence=1.0).relevant is True,
                "relevant field OK",
            )
        )
        results.append(
            _check(
                "Verify: VerifyResult",
                VerifyResult(
                    memory_id="m1",
                    original_rank=0,
                    original_score=0.0,
                    demoted_score=0.0,
                    verdict=Verdict(relevant=True, confidence=1.0),
                    demoted=False,
                ).demoted
                is False,
                "demoted field OK",
            )
        )
        results.append(
            _check("Verify: verify_top_k_sync callable", callable(verify_top_k_sync), "")
        )
    except Exception as e:
        results.append(_check("Verify: 模块加载", False, str(e)))

    # 5. Purge + Archive 模块
    try:
        from packages.memory.archive import ArchivedRecord, InMemoryArchiveStore
        from packages.memory.governance.purge import PurgeReport, run_purge

        results.append(
            _check(
                "Purge: PurgeReport",
                PurgeReport().expired_deleted == 0,
                "expired_deleted default 0 OK",
            )
        )
        results.append(_check("Purge: run_purge callable", callable(run_purge), ""))
        results.append(
            _check(
                "Archive: ArchivedRecord",
                ArchivedRecord(
                    archive_id="a1",
                    memory_id="m1",
                    tenant_id="t1",
                    scope="user",
                    scope_id="u1",
                    content="",
                    created_at=0.0,
                    archived_at=0.0,
                    purge_reason="test",
                    original_weight=0.0,
                    access_count=0,
                ).purge_reason
                == "test",
                "purge_reason field OK",
            )
        )
        results.append(_check("Archive: InMemoryArchiveStore", callable(InMemoryArchiveStore), ""))
    except Exception as e:
        results.append(_check("Purge/Archive: 模块加载", False, str(e)))

    # 6. Governance Worker CLI
    try:
        from packages.memory.governance_worker import main as gov_worker_main

        results.append(_check("Worker: CLI main callable", callable(gov_worker_main), ""))
    except Exception as e:
        results.append(_check("Worker: 模块加载", False, str(e)))

    # 7. Governance REST API 路由
    try:
        from apps.gateway.memory_routes import router

        routes = [r.path for r in router.routes]
        gov_routes = [r for r in routes if "governance" in r or "archive" in r]
        has_gov_route = len(gov_routes) >= 3
        has_feedback = any("feedback" in r for r in routes)
        results.append(_check("Routes: governance endpoints", has_gov_route, str(gov_routes)))
        results.append(_check("Routes: feedback endpoint", has_feedback, ""))
    except Exception as e:
        results.append(_check("Routes: 加载", False, str(e)))

    # 8. Metrics 指标
    try:
        from packages.memory.metrics import get_memory_metrics, reset_metrics_for_tests

        reset_metrics_for_tests()
        m = get_memory_metrics()
        m.record_dedup_skipped(tenant_id="t1", scope="user")
        m.record_dedup_merged(tenant_id="t1", scope="user")
        m.record_verify_check(tenant_id="t1", scope="user")
        m.record_verify_demoted(tenant_id="t1", scope="user")
        m.record_purge(reason="expired")
        m.record_archive()
        m.record_governance_run(duration_seconds=1.0)
        m.record_library_total(tenant_id="t1", scope="user", count=42)
        m.record_library_expired(tenant_id="t1", scope="user", count=3)

        prom = m.prometheus_text()
        expected_metrics = [
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
        all_present = all(m_name in prom for m_name in expected_metrics)
        missing_metrics = [m_name for m_name in expected_metrics if m_name not in prom]
        results.append(
            _check(
                "Metrics: 10 个治理指标",
                all_present,
                f"missing={missing_metrics if missing_metrics else 'none'}",
            )
        )

        # Verify metric values
        results.append(
            _check(
                "Metrics: dedup_skipped 计数",
                'memory_dedup_skipped_total{tenant_id="t1",scope="user"} 1' in prom,
                "",
            )
        )
        results.append(
            _check(
                "Metrics: governance_purge 计数",
                'governance_purge_total{reason="expired"} 1' in prom,
                "",
            )
        )
        results.append(
            _check("Metrics: governance_runtime", "governance_runtime_seconds" in prom, "")
        )
        results.append(
            _check(
                "Metrics: memory_library_total",
                'memory_library_total{tenant_id="t1",scope="user"} 42' in prom,
                "",
            )
        )

        reset_metrics_for_tests()
    except Exception as e:
        results.append(_check("Metrics: 加载", False, str(e)))

    # 9. Store 治理集成
    # 由于运行在 event loop 中，无法使用 asyncio.run()，
    # 集成测试由 pytest tests/test_memory_dedup.py 等覆盖
    # 此处仅验证模块导入和 dataclass 结构
    try:
        from packages.memory import InMemoryMemoryStore

        store = InMemoryMemoryStore()
        # just verify instantiation works
        results.append(_check("Store: InMemoryMemoryStore 实例化", store is not None, ""))
    except Exception as e:
        results.append(_check("Store: 实例化", False, str(e)))

    except Exception as e:
        results.append(_check("Integration: 测试", False, str(e)))

    return results


def main() -> int:
    results = run_governance_smoke()
    passed = 0
    failed = 0
    for line in results:
        print(line)
        if line.startswith("  [PASS]"):
            passed += 1
        elif line.startswith("  [FAIL]"):
            failed += 1
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"结果: {passed}/{total} passed")
    if failed:
        print(f"失败: {failed}")
    print(f"{'=' * 50}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
