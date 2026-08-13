#!/usr/bin/env python3
"""tests/test_memory_governance_e2e.py — Issue #207 Memory Governance Metrics + Integration E2E.

Comprehensive E2E test for the full governance pipeline:
- Write path: quality_filter -> dedup_filter -> store
- Read path: weighted retrieval -> access_count auto-increment
- Rerank path: LLM judge rerank (mocked) -> filtered results
- Prometheus metrics exposure
- Config integration (disabling governance bypasses filters)

Requires existing test files:
    tests/test_memory_quality.py (#205)
    tests/test_experience_quality.py (#206)
    tests/test_experience_dedup.py (#206)

Run:
    python3 tests/test_memory_governance_e2e.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.experience_store import (  # noqa: E402
    ExperienceRecord,
    InMemoryExperienceStore,
    build_experience_record,
    clear_rerank_cache,
    get_experience_metrics,
    rerank_experiences,
    reset_experience_metrics_for_tests,
    reset_experience_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402
from packages.memory import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryGovernanceConfig,
    MemoryRecord,
    get_memory_metrics,
)
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import reset_memory_store_for_tests  # noqa: E402


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()
    reset_experience_metrics_for_tests()
    reset_experience_store_for_tests()
    clear_rerank_cache()


# ------------------------------------------------------------------------- #
# E2E — Write path: quality_filter -> dedup_filter -> store
# ------------------------------------------------------------------------- #


def _make_plan(goal: str = "test goal") -> AgentPlan:
    return AgentPlan(goal=goal, steps=[PlanStep(id="s1", description="do thing", depends_on=[])])


class TestGovernanceE2EWritePath:
    """Full write pipeline: quality -> dedup -> store."""

    def test_e2e_quality_rejects_low_quality_no_store(self):
        """quality_filter rejects low-quality content -> record not stored."""
        _setup()
        config = MemoryGovernanceConfig(min_content_length=20, quality_filter_enabled=True)
        mstore = InMemoryMemoryStore(governance_config=config)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            # Memory: short content -> rejected
            mr = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="short",
            )
            await mstore.add(mr)
            got = await mstore.get("m1")
            assert got is None, "short memory content should not be stored"

            # Experience: short lessons -> rejected
            er = build_experience_record(
                tenant_id="t2", goal="test", plan=_make_plan(), outcome="success", lessons="short",
            )
            await estore.store(er)
            got_e = await estore.get(er.experience_id)
            assert got_e is None, "short experience lessons should not be stored"

        asyncio.run(run())
        print("PASS test_e2e_quality_rejects_low_quality_no_store")

    def test_e2e_dedup_skips_complete_duplicate(self):
        """dedup_filter skips exact duplicate -> only one record stored."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            emb = [1.0, 0.0, 0.0]
            r1 = build_experience_record(
                tenant_id="t1", goal="goal A", plan=_make_plan("goal A"),
                outcome="success", lessons="useful lesson one", embedding=emb,
            )
            r2 = build_experience_record(
                tenant_id="t1", goal="goal B", plan=_make_plan("goal B"),
                outcome="success", lessons="useful lesson two", embedding=emb,
            )
            await estore.store(r1)
            await estore.store(r2)

            got1 = await estore.get(r1.experience_id)
            assert got1 is not None, "first record should exist"
            got2 = await estore.get(r2.experience_id)
            assert got2 is None, "second duplicate should be skipped via dedup"

            # Exactly one record stored
            all_records = await estore.list_all()
            assert len(all_records) == 1

        asyncio.run(run())
        print("PASS test_e2e_dedup_skips_complete_duplicate")

    def test_e2e_dedup_merges_lessons_high_similarity(self):
        """dedup_filter merges lessons for high similarity but below skip threshold."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            # Cosine([1,0], [0.87,0.49]) ~ 0.87 -> merge_lessons
            r1 = build_experience_record(
                tenant_id="t1", goal="goal A", plan=_make_plan("goal A"),
                outcome="success", lessons="original lesson content", embedding=[1.0, 0.0],
            )
            r2 = build_experience_record(
                tenant_id="t1", goal="goal B", plan=_make_plan("goal B"),
                outcome="success", lessons="new lesson insights", embedding=[0.87, 0.49],
            )
            await estore.store(r1)
            await estore.store(r2)

            got1 = await estore.get(r1.experience_id)
            assert got1 is not None
            assert "original lesson content" in got1.lessons
            assert "new lesson insights" in got1.lessons, "lessons should be merged"

            got2 = await estore.get(r2.experience_id)
            assert got2 is None, "merged record not stored separately"

        asyncio.run(run())
        print("PASS test_e2e_dedup_merges_lessons_high_similarity")

    def test_e2e_dedup_stores_low_similarity(self):
        """dedup_filter stores records with low similarity normally."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            # Orthogonal embeddings -> cosine 0.0 -> store
            r1 = build_experience_record(
                tenant_id="t1", goal="goal A", plan=_make_plan("goal A"),
                outcome="success", lessons="lesson A", embedding=[1.0, 0.0],
            )
            r2 = build_experience_record(
                tenant_id="t1", goal="goal B", plan=_make_plan("goal B"),
                outcome="success", lessons="lesson B", embedding=[0.0, 1.0],
            )
            await estore.store(r1)
            await estore.store(r2)

            got1 = await estore.get(r1.experience_id)
            got2 = await estore.get(r2.experience_id)
            assert got1 is not None
            assert got2 is not None
            assert got1.lessons == "lesson A"
            assert got2.lessons == "lesson B"

        asyncio.run(run())
        print("PASS test_e2e_dedup_stores_low_similarity")


# ------------------------------------------------------------------------- #
# E2E — Read path: weighted retrieval + access_count
# ------------------------------------------------------------------------- #


class TestGovernanceE2EReadPath:
    """Read path: weighted retrieval, access_count auto-increment."""

    def test_weighted_retrieval_ranks_high_weight_higher(self):
        """Weighted retrieval ranks higher-weight records higher."""
        _setup()
        mstore = InMemoryMemoryStore(
            governance_config=MemoryGovernanceConfig(min_content_length=1)
        )

        async def run():
            r1 = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user", scope_id="u1",
                content="low weight memory", embedding=[1.0, 0.0, 0.0], weight=0.1,
            )
            r2 = MemoryRecord(
                memory_id="m2", tenant_id="t1", scope="user", scope_id="u1",
                content="high weight memory", embedding=[0.99, 0.01, 0.0], weight=10.0,
            )
            await mstore.add(r1)
            await mstore.add(r2)

            # Semantic search: r2 has higher weight so should rank first
            results = await mstore.search(
                tenant_id="t1", scope="user", scope_id="u1",
                query="memory", top_k=2, query_embedding=[1.0, 0.0, 0.0],
            )
            assert len(results) >= 2
            # High-weight record should be first despite slightly lower cosine
            assert results[0].memory_id == "m2", (
                f"expected m2 (high weight) first, got {results[0].memory_id}"
            )

        asyncio.run(run())
        print("PASS test_weighted_retrieval_ranks_high_weight_higher")

    def test_access_count_increments_on_get(self):
        """access_count auto-increments after get()."""
        _setup()
        mstore = InMemoryMemoryStore(
            governance_config=MemoryGovernanceConfig(min_content_length=1)
        )

        async def run():
            r = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user",
                scope_id="u1", content="test memory for access counting",
            )
            await mstore.add(r)

            got1 = await mstore.get("m1")
            assert got1 is not None
            assert got1.access_count >= 1
            count_after_first_get = got1.access_count

            got2 = await mstore.get("m1")
            assert got2 is not None
            assert got2.access_count >= count_after_first_get + 1, (
                f"access_count should increment: {got2.access_count} >= {count_after_first_get + 1}"
            )

        asyncio.run(run())
        print("PASS test_access_count_increments_on_get")

    def test_access_count_increments_on_search(self):
        """access_count auto-increments after search()."""
        _setup()
        mstore = InMemoryMemoryStore(
            governance_config=MemoryGovernanceConfig(min_content_length=1)
        )

        async def run():
            r = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user",
                scope_id="u1", content="test memory for search access counting",
            )
            await mstore.add(r)

            results = await mstore.search(
                tenant_id="t1", scope="user", scope_id="u1",
                query="test memory", top_k=5,
            )
            assert len(results) > 0
            assert results[0].access_count >= 1

        asyncio.run(run())
        print("PASS test_access_count_increments_on_search")

    def test_experience_access_count_increments_on_get(self):
        """ExperienceStore.get() auto-increments access_count."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            r = build_experience_record(
                tenant_id="t1", goal="test", plan=_make_plan(),
                outcome="success", lessons="experience for access counting",
            )
            await estore.store(r)

            got1 = await estore.get(r.experience_id)
            assert got1 is not None
            count_after_first = got1.access_count

            got2 = await estore.get(r.experience_id)
            assert got2 is not None
            assert got2.access_count >= count_after_first + 1

        asyncio.run(run())
        print("PASS test_experience_access_count_increments_on_get")


# ------------------------------------------------------------------------- #
# E2E — Rerank path (mocked LLM judge)
# ------------------------------------------------------------------------- #


class MockRoute:
    """Mock route response for rerank."""

    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self.body = body


class TestGovernanceE2ERerank:
    """Rerank path: LLM judge rerank (mocked) -> filtered results."""

    @patch("packages.platform.forward_with_model_router")
    def test_e2e_rerank_filters_irrelevant(self, mock_forward):
        """Rerank filters irrelevant experiences via mocked LLM."""
        _setup()
        clear_rerank_cache()

        exps = [
            ExperienceRecord(
                experience_id="exp-1", tenant_id="t1", task_signature="sig1",
                goal="build login page", plan=_make_plan(), tool_calls=[],
                outcome="success", lessons="learned about auth", created_at=time.time(),
            ),
            ExperienceRecord(
                experience_id="exp-2", tenant_id="t1", task_signature="sig2",
                goal="implement logging", plan=_make_plan(), tool_calls=[],
                outcome="success", lessons="learned about log levels", created_at=time.time(),
            ),
        ]

        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true, "reason": "directly related"},'
                                '{"index": 1, "relevant": false, "reason": "unrelated"}]'
                            )
                        }
                    }
                ]
            },
        )

        async def run():
            result = await rerank_experiences("user authentication", exps, max_relevant=5)
            # Only exp-1 (login page) should remain
            assert len(result) == 1
            assert result[0].experience_id == "exp-1"

        asyncio.run(run())
        print("PASS test_e2e_rerank_filters_irrelevant")

    @patch("packages.platform.forward_with_model_router")
    def test_e2e_rerank_maintains_relevant(self, mock_forward):
        """Rerank maintains all relevant experiences."""
        _setup()
        clear_rerank_cache()

        exps = [
            ExperienceRecord(
                experience_id="exp-1", tenant_id="t1", task_signature="sig1",
                goal="build login page", plan=_make_plan(), tool_calls=[],
                outcome="success", lessons="auth lessons", created_at=time.time(),
            ),
            ExperienceRecord(
                experience_id="exp-2", tenant_id="t1", task_signature="sig2",
                goal="implement auth tokens", plan=_make_plan(), tool_calls=[],
                outcome="success", lessons="token lessons", created_at=time.time(),
            ),
        ]

        mock_forward.return_value = MockRoute(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '[{"index": 0, "relevant": true, "reason": "auth"},'
                                '{"index": 1, "relevant": true, "reason": "tokens"}]'
                            )
                        }
                    }
                ]
            },
        )

        async def run():
            result = await rerank_experiences("user authentication", exps, max_relevant=5)
            assert len(result) == 2

        asyncio.run(run())
        print("PASS test_e2e_rerank_maintains_relevant")

    def test_e2e_rerank_handles_empty_input(self):
        """Rerank returns empty for empty experiences list."""
        _setup()

        async def run():
            result = await rerank_experiences("test goal", [], max_relevant=5)
            assert result == []

        asyncio.run(run())
        print("PASS test_e2e_rerank_handles_empty_input")

    @patch("packages.platform.forward_with_model_router")
    def test_e2e_rerank_fail_open_on_error(self, mock_forward):
        """Rerank fails open when LLM call fails."""
        _setup()
        clear_rerank_cache()

        exps = [
            ExperienceRecord(
                experience_id="exp-1", tenant_id="t1", task_signature="sig1",
                goal="build login", plan=_make_plan(), tool_calls=[],
                outcome="success", lessons="lessons", created_at=time.time(),
            ),
        ]

        mock_forward.side_effect = RuntimeError("LLM API down")

        async def run():
            # Should return all experiences on failure
            result = await rerank_experiences("test goal", exps, max_relevant=5)
            assert len(result) == 1

        asyncio.run(run())
        print("PASS test_e2e_rerank_fail_open_on_error")


# ------------------------------------------------------------------------- #
# E2E — Prometheus metrics exposure
# ------------------------------------------------------------------------- #


class TestGovernanceE2EMetrics:
    """Verify Prometheus-style metrics exposure."""

    def test_memory_metrics_prometheus_exposes_quality_rejected(self):
        """MemoryMetrics.prometheus_text() exposes quality_rejected counter."""
        _setup()
        config = MemoryGovernanceConfig(min_content_length=1, quality_filter_enabled=True)
        mstore = InMemoryMemoryStore(governance_config=config)

        async def run():
            r = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="",
            )
            await mstore.add(r)

        asyncio.run(run())
        prom = get_memory_metrics().prometheus_text()
        assert "memory_quality_rejected_total" in prom
        assert 'tenant_id="t1"' in prom
        assert 'scope="user"' in prom
        print("PASS test_memory_metrics_prometheus_exposes_quality_rejected")

    def test_experience_metrics_prometheus_exposes_all_governance_counters(self):
        """ExperienceMetrics prometheus_text() exposes governance counters."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            # Trigger quality_rejected
            r1 = build_experience_record(
                tenant_id="t1", goal="test", plan=_make_plan(),
                outcome="success", lessons="",
            )
            await estore.store(r1)

            # Trigger dedup by storing one then trying duplicate
            emb = [1.0, 0.0, 0.0]
            r2 = build_experience_record(
                tenant_id="t1", goal="goal A", plan=_make_plan("goal A"),
                outcome="success", lessons="lesson one", embedding=emb,
            )
            await estore.store(r2)
            r3 = build_experience_record(
                tenant_id="t1", goal="goal B", plan=_make_plan("goal B"),
                outcome="success", lessons="lesson two", embedding=emb,
            )
            await estore.store(r3)

        asyncio.run(run())
        prom = get_experience_metrics().prometheus_text()
        assert "experience_quality_rejected_total" in prom
        assert "experience_dedup_skipped_total" in prom
        assert "experience_dedup_merged_total" in prom
        assert "experience_stores_total" in prom
        assert "experience_retrieves_total" in prom
        assert "experience_store_errors_total" in prom
        assert 'tenant_id="t1"' in prom
        print("PASS test_experience_metrics_prometheus_exposes_all_governance_counters")

    def test_memory_metrics_has_all_counters_in_prometheus(self):
        """MemoryMetrics prometheus text contains all expected counter lines."""
        _setup()
        mm = get_memory_metrics()
        # Trigger a few actions to populate counters
        mm.record_add(tenant_id="t1", scope="user")
        mm.record_search(tenant_id="t1", scope="user")
        mm.record_cache_hit(tenant_id="t1", scope="user")
        mm.record_cache_miss(tenant_id="t1", scope="user")
        mm.record_store_error(tenant_id="t1", scope="user")
        mm.record_quality_rejected(tenant_id="t1", scope="user")
        mm.record_search_latency(tenant_id="t1", scope="user", latency_ms=10.0)

        prom = mm.prometheus_text()

        expected_metrics = [
            "memory_adds_total",
            "memory_searches_total",
            "memory_cache_hits_total",
            "memory_cache_misses_total",
            "memory_store_errors_total",
            "memory_quality_rejected_total",
            "memory_search_latency_ms_p95",
        ]
        for metric in expected_metrics:
            assert metric in prom, f"Expected {metric} in prometheus output"

        print("PASS test_memory_metrics_has_all_counters_in_prometheus")


# ------------------------------------------------------------------------- #
# E2E — Config integration (disabling governance)
# ------------------------------------------------------------------------- #


class TestGovernanceE2EConfig:
    """Config integration: disabling governance bypasses filters."""

    def test_memory_config_disabled_bypasses_quality_filter(self):
        """Disabling quality_filter_enabled allows empty content through."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=False, min_content_length=20)
        mstore = InMemoryMemoryStore(governance_config=config)

        async def run():
            r = MemoryRecord(
                memory_id="m1", tenant_id="t1", scope="user", scope_id="u1", content="",
            )
            await mstore.add(r)
            got = await mstore.get("m1")
            assert got is not None, "empty content should be stored when filter disabled"
            assert got.content == ""

        asyncio.run(run())
        print("PASS test_memory_config_disabled_bypasses_quality_filter")

    def test_experience_config_disabled_bypasses_quality_filter(self):
        """Disabling quality_filter allows empty lessons through."""
        _setup()
        config = MemoryGovernanceConfig(quality_filter_enabled=False, min_content_length=20)
        estore = InMemoryExperienceStore(governance_config=config)

        async def run():
            r = build_experience_record(
                tenant_id="t1", goal="test", plan=_make_plan(),
                outcome="success", lessons="",
            )
            await estore.store(r)
            got = await estore.get(r.experience_id)
            assert got is not None, "empty lessons should be stored when filter disabled"

        asyncio.run(run())
        print("PASS test_experience_config_disabled_bypasses_quality_filter")

    def test_config_injectable_at_construction(self):
        """MemoryGovernanceConfig is injectable at both MemoryStore and ExperienceStore construction."""
        _setup()
        config = MemoryGovernanceConfig(
            quality_filter_enabled=True,
            min_content_length=50,
            dedup_skip_threshold=0.98,
            dedup_merge_threshold=0.90,
        )

        # MemoryStore
        mstore = InMemoryMemoryStore(governance_config=config)
        assert mstore._governance_config is config, "config should be the same object"
        assert mstore._governance_config.min_content_length == 50

        # ExperienceStore
        estore = InMemoryExperienceStore(governance_config=config)
        assert estore._governance_config is config, "config should be the same object"
        assert estore._governance_config.dedup_skip_threshold == 0.98

        print("PASS test_config_injectable_at_construction")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    # Instantiate test classes
    tests: list[tuple[str, list]] = [
        ("Write Path", [
            TestGovernanceE2EWritePath().test_e2e_quality_rejects_low_quality_no_store,
            TestGovernanceE2EWritePath().test_e2e_dedup_skips_complete_duplicate,
            TestGovernanceE2EWritePath().test_e2e_dedup_merges_lessons_high_similarity,
            TestGovernanceE2EWritePath().test_e2e_dedup_stores_low_similarity,
        ]),
        ("Read Path", [
            TestGovernanceE2EReadPath().test_weighted_retrieval_ranks_high_weight_higher,
            TestGovernanceE2EReadPath().test_access_count_increments_on_get,
            TestGovernanceE2EReadPath().test_access_count_increments_on_search,
            TestGovernanceE2EReadPath().test_experience_access_count_increments_on_get,
        ]),
        ("Rerank Path", [
            TestGovernanceE2ERerank().test_e2e_rerank_filters_irrelevant,
            TestGovernanceE2ERerank().test_e2e_rerank_maintains_relevant,
            TestGovernanceE2ERerank().test_e2e_rerank_handles_empty_input,
            TestGovernanceE2ERerank().test_e2e_rerank_fail_open_on_error,
        ]),
        ("Metrics", [
            TestGovernanceE2EMetrics().test_memory_metrics_prometheus_exposes_quality_rejected,
            TestGovernanceE2EMetrics().test_experience_metrics_prometheus_exposes_all_governance_counters,
            TestGovernanceE2EMetrics().test_memory_metrics_has_all_counters_in_prometheus,
        ]),
        ("Config", [
            TestGovernanceE2EConfig().test_memory_config_disabled_bypasses_quality_filter,
            TestGovernanceE2EConfig().test_experience_config_disabled_bypasses_quality_filter,
            TestGovernanceE2EConfig().test_config_injectable_at_construction,
        ]),
    ]

    total = 0
    failed = 0
    for group_name, group_tests in tests:
        total += len(group_tests)
        for t in group_tests:
            try:
                t()
            except AssertionError as e:
                print(f"FAIL {t.__name__}: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
                failed += 1

    print(f"\n{total - failed}/{total} passed")
    if failed > 0:
        print(f"{failed} test(s) failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
