#!/usr/bin/env python3
"""Prompt A/B 实验单元测试 — Phase F #30

运行：
    python3 tests/test_prompt_experiment.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.prompt import (  # noqa: E402
    ExperimentError,
    ExperimentStore,
    ExperimentVariant,
    PromptRegistry,
    VariantMetrics,
    init_experiment_store,
    init_registry,
    reset_experiment_store_for_tests,
    reset_registry_for_tests,
)


def _setup(tmpdir: Path) -> tuple[PromptRegistry, ExperimentStore]:
    reset_registry_for_tests()
    reset_experiment_store_for_tests()
    yaml_path = tmpdir / "prompts.yaml"
    yaml_path.write_text(
        """
prompts:
  - prompt_id: rag_query
    version: 1
    status: active
    content: "V1 {{context}} {{query}}"
    created_by: test
  - prompt_id: rag_query
    version: 2
    status: draft
    content: "V2 {{context}} {{query}}"
    created_by: test
""",
        encoding="utf-8",
    )
    reg = init_registry(
        yaml_path=yaml_path,
        overrides_path=tmpdir / "overrides.json",
    )
    store = init_experiment_store(storage_path=tmpdir / "experiments.json")
    return reg, store


def test_create_experiment_validates_percent():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        # percent 之和 != 100
        try:
            store.create_experiment(
                prompt_id="rag_query",
                variants=[
                    ExperimentVariant(version=1, percent=60),
                    ExperimentVariant(version=2, percent=30),  # 总 90
                ],
            )
            assert False, "expected ExperimentError"
        except ExperimentError as e:
            assert e.code == "INVALID_PERCENT"
        print("PASS test_create_experiment_validates_percent")


def test_create_experiment_requires_two_variants():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        try:
            store.create_experiment(
                prompt_id="rag_query",
                variants=[ExperimentVariant(version=1, percent=100)],
            )
            assert False, "expected ExperimentError"
        except ExperimentError as e:
            assert e.code == "INVALID_VARIANTS"
        print("PASS test_create_experiment_requires_two_variants")


def test_create_experiment_success():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=10,
        )
        assert exp.status == "running"
        assert len(exp.variants) == 2
        assert exp.prompt_id == "rag_query"
        print("PASS test_create_experiment_success")


def test_one_running_experiment_per_prompt():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        try:
            store.create_experiment(
                prompt_id="rag_query",
                variants=[
                    ExperimentVariant(version=1, percent=50),
                    ExperimentVariant(version=2, percent=50),
                ],
            )
            assert False, "expected EXPERIMENT_RUNNING"
        except ExperimentError as e:
            assert e.code == "EXPERIMENT_RUNNING"
        print("PASS test_one_running_experiment_per_prompt")


def test_pick_variant_deterministic():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        # 同一 bucket_key 应始终返回相同 variant
        picked1 = store.pick_variant(
            prompt_id="rag_query", tenant_id="global", bucket_key="user-123"
        )
        picked2 = store.pick_variant(
            prompt_id="rag_query", tenant_id="global", bucket_key="user-123"
        )
        assert picked1 is not None
        assert picked2 is not None
        assert picked1[1].version == picked2[1].version
        print("PASS test_pick_variant_deterministic")


def test_pick_variant_distribution():
    """10000 次分桶，验证 50/50 分布近似"""
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=70),
                ExperimentVariant(version=2, percent=30),
            ],
        )
        counts = {1: 0, 2: 0}
        for i in range(10000):
            picked = store.pick_variant(
                prompt_id="rag_query",
                tenant_id="global",
                bucket_key=f"user-{i}",
            )
            assert picked is not None
            counts[picked[1].version] += 1
        # 70/30 分布，允许 ±5%
        assert 6500 < counts[1] < 7500, f"v1 count={counts[1]}"
        assert 2500 < counts[2] < 3500, f"v2 count={counts[2]}"
        print(
            f"PASS test_pick_variant_distribution (v1={counts[1]}, v2={counts[2]})"
        )


def test_record_request_and_metrics():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=5,
        )
        store.record_request(
            experiment_id=exp.experiment_id,
            version=1,
            latency_ms=120.0,
            tokens=30,
            error=False,
        )
        store.record_request(
            experiment_id=exp.experiment_id,
            version=1,
            latency_ms=200.0,
            tokens=40,
            error=True,
        )
        m = store.get_metrics(experiment_id=exp.experiment_id, version=1)
        assert m is not None
        assert m.requests == 2
        assert m.tokens_used == 70
        assert m.errors == 1
        assert len(m.latencies_ms) == 2
        print("PASS test_record_request_and_metrics")


def test_record_quality_feedback():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        store.record_quality(
            experiment_id=exp.experiment_id, version=1, score=0.8
        )
        store.record_quality(
            experiment_id=exp.experiment_id, version=1, score=1.0
        )
        m = store.get_metrics(experiment_id=exp.experiment_id, version=1)
        assert len(m.quality_scores) == 2
        assert 0.89 < m._avg(m.quality_scores) < 0.91
        print("PASS test_record_quality_feedback")


def test_record_quality_invalid_score():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        try:
            store.record_quality(
                experiment_id=exp.experiment_id, version=1, score=1.5
            )
            assert False
        except ExperimentError as e:
            assert e.code == "INVALID_SCORE"
        print("PASS test_record_quality_invalid_score")


def test_auto_winner_quality():
    """v1 质量明显高于 v2，达到 min_samples 后自动胜出"""
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=10,
            success_metric="quality",
            winner_margin=0.1,
        )
        # v1: 高质量 10 次
        for _ in range(10):
            store.record_request(
                experiment_id=exp.experiment_id, version=1, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=1, score=0.9
            )
        # v2: 低质量 10 次
        for _ in range(10):
            store.record_request(
                experiment_id=exp.experiment_id, version=2, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=2, score=0.5
            )
        winner = store.maybe_auto_winner(exp.experiment_id)
        assert winner == 1, f"expected winner=1, got {winner}"
        # 实验状态应变为 stopped
        exp2 = store.get_experiment(exp.experiment_id)
        assert exp2.status == "stopped"
        assert exp2.winner_version == 1
        print("PASS test_auto_winner_quality")


def test_auto_winner_not_enough_samples():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=100,
        )
        store.record_request(
            experiment_id=exp.experiment_id, version=1, latency_ms=100, tokens=10
        )
        winner = store.maybe_auto_winner(exp.experiment_id)
        assert winner is None
        print("PASS test_auto_winner_not_enough_samples")


def test_auto_winner_no_margin():
    """两个 variant 表现相近，margin 不足，不应自动胜出"""
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=10,
            success_metric="quality",
            winner_margin=0.5,  # 高门槛
        )
        for _ in range(10):
            store.record_request(
                experiment_id=exp.experiment_id, version=1, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=1, score=0.8
            )
            store.record_request(
                experiment_id=exp.experiment_id, version=2, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=2, score=0.75
            )
        winner = store.maybe_auto_winner(exp.experiment_id)
        assert winner is None, "margin 不足，不应自动胜出"
        print("PASS test_auto_winner_no_margin")


def test_stop_experiment():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        stopped = store.stop_experiment(exp.experiment_id)
        assert stopped.status == "stopped"
        assert stopped.stopped_at is not None
        # 再次 stop 应失败
        try:
            store.stop_experiment(exp.experiment_id)
            assert False
        except ExperimentError as e:
            assert e.code == "NOT_RUNNING"
        print("PASS test_stop_experiment")


def test_promote_winner():
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=5,
        )
        # 先模拟自动胜出
        for _ in range(5):
            store.record_request(
                experiment_id=exp.experiment_id, version=1, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=1, score=0.9
            )
            store.record_request(
                experiment_id=exp.experiment_id, version=2, latency_ms=100, tokens=10
            )
            store.record_quality(
                experiment_id=exp.experiment_id, version=2, score=0.4
            )
        store.maybe_auto_winner(exp.experiment_id)
        # 手动 promote
        winner_v = store.promote_winner(exp.experiment_id)
        assert winner_v == 1
        exp2 = store.get_experiment(exp.experiment_id)
        assert exp2.status == "promoted"
        print("PASS test_promote_winner")


def test_persist_and_reload():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        reg, store = _setup(tmp)
        exp = store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
            min_samples=5,
        )
        store.record_request(
            experiment_id=exp.experiment_id, version=1, latency_ms=100, tokens=10
        )
        store.record_quality(
            experiment_id=exp.experiment_id, version=1, score=0.9
        )
        # 新实例加载同一文件
        store2 = ExperimentStore(storage_path=tmp / "experiments.json")
        store2.load()
        exp2 = store2.get_experiment(exp.experiment_id)
        assert exp2 is not None
        assert exp2.status == "running"
        m = store2.get_metrics(experiment_id=exp.experiment_id, version=1)
        assert m is not None
        assert m.requests == 1
        assert len(m.quality_scores) == 1
        print("PASS test_persist_and_reload")


def test_registry_render_with_experiment():
    """registry.render_with_experiment 应按分桶返回版本"""
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        store.create_experiment(
            prompt_id="rag_query",
            variants=[
                ExperimentVariant(version=1, percent=50),
                ExperimentVariant(version=2, percent=50),
            ],
        )
        # 分桶到 v1 或 v2
        rendered, entry, exp_info = reg.render_with_experiment(
            "rag_query",
            {"context": "CTX", "query": "Q"},
            bucket_key="user-1",
            experiment_store=store,
        )
        assert entry is not None
        assert entry.version in (1, 2)
        assert exp_info["experiment_id"] is not None
        assert exp_info["variant_version"] == entry.version
        # 同一 user 应始终返回同一版本
        rendered2, entry2, exp_info2 = reg.render_with_experiment(
            "rag_query",
            {"context": "CTX", "query": "Q"},
            bucket_key="user-1",
            experiment_store=store,
        )
        assert entry.version == entry2.version
        print("PASS test_registry_render_with_experiment")


def test_registry_render_with_experiment_no_experiment():
    """无运行中实验时回退到 active"""
    with tempfile.TemporaryDirectory() as d:
        reg, store = _setup(Path(d))
        rendered, entry, exp_info = reg.render_with_experiment(
            "rag_query",
            {"context": "CTX", "query": "Q"},
            bucket_key="user-1",
            experiment_store=store,
        )
        assert entry.version == 1  # active
        assert exp_info["experiment_id"] is None
        print("PASS test_registry_render_with_experiment_no_experiment")


def main() -> int:
    tests = [
        test_create_experiment_validates_percent,
        test_create_experiment_requires_two_variants,
        test_create_experiment_success,
        test_one_running_experiment_per_prompt,
        test_pick_variant_deterministic,
        test_pick_variant_distribution,
        test_record_request_and_metrics,
        test_record_quality_feedback,
        test_record_quality_invalid_score,
        test_auto_winner_quality,
        test_auto_winner_not_enough_samples,
        test_auto_winner_no_margin,
        test_stop_experiment,
        test_promote_winner,
        test_persist_and_reload,
        test_registry_render_with_experiment,
        test_registry_render_with_experiment_no_experiment,
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
