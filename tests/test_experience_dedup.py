#!/usr/bin/env python3
"""ExperienceStore dedup_filter 单元测试 — Issue #206

运行：
    python3 tests/test_experience_dedup.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.experience_store import (  # noqa: E402
    ExperienceRecord,
    InMemoryExperienceStore,
    build_experience_record,
    dedup_filter,
    get_experience_metrics,
    reset_experience_metrics_for_tests,
    reset_experience_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402
from packages.memory.config import MemoryGovernanceConfig  # noqa: E402


def _setup():
    reset_experience_metrics_for_tests()
    reset_experience_store_for_tests()


def _make_plan(goal: str = "test goal") -> AgentPlan:
    return AgentPlan(goal=goal, steps=[PlanStep(id="s1", description="do thing", depends_on=[])])


def _make_record(
    experience_id: str,
    lessons: str = "some useful lessons",
    embedding: list[float] | None = None,
    goal: str = "test goal",
) -> ExperienceRecord:
    """Create an ExperienceRecord with a specific experience_id."""
    return ExperienceRecord(
        experience_id=experience_id,
        tenant_id="t1",
        task_signature="test_sig_1234567890",
        goal=goal,
        plan=_make_plan(goal),
        tool_calls=[],
        outcome="success",
        lessons=lessons,
        created_at=time.time(),
        embedding=embedding,
    )


# ------------------------------------------------------------------------- #
# dedup_filter unit tests
# ------------------------------------------------------------------------- #


def test_dedup_filter_skip_identical_vectors():
    """相同 embedding (cosine=1.0) → skip."""
    emb = [1.0, 0.0, 0.0]
    existing = [
        _make_record(
            experience_id="existing1",
            lessons="old lesson",
            embedding=[1.0, 0.0, 0.0],
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    assert action == "skip", f"expected skip, got {action}"
    assert merged_id == "existing1"
    print("PASS test_dedup_filter_skip_identical_vectors")


def test_dedup_filter_skip_high_similarity():
    """相似度 >= 0.95 → skip."""
    emb = [1.0, 0.0, 0.0]
    # Cosine of [1,0,0] and [0.99, 0.01, 0.0] >= 0.95
    existing = [
        _make_record(
            experience_id="existing1",
            lessons="old lesson",
            embedding=[0.99, 0.01, 0.0],
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    assert action == "skip", f"expected skip, got {action}"
    assert merged_id == "existing1"
    print("PASS test_dedup_filter_skip_high_similarity")


def test_dedup_filter_merge_lessons():
    """0.85 <= 相似度 < 0.95 → merge_lessons."""
    # Cosine of [1,0] and [0.87, 0.49] ≈ 0.87
    emb = [1.0, 0.0]
    existing = [
        _make_record(
            experience_id="existing1",
            lessons="old lesson",
            embedding=[0.87, 0.49],
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    assert action == "merge_lessons", f"expected merge_lessons, got {action}"
    assert merged_id == "existing1"
    print("PASS test_dedup_filter_merge_lessons")


def test_dedup_filter_store_low_similarity():
    """相似度 < 0.85 → store."""
    emb = [1.0, 0.0]
    # Orthogonal = cosine 0.0
    existing = [
        _make_record(
            experience_id="existing1",
            lessons="old lesson",
            embedding=[0.0, 1.0],
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    assert action == "store", f"expected store, got {action}"
    assert merged_id is None
    print("PASS test_dedup_filter_store_low_similarity")


def test_dedup_filter_no_embedding_returns_store():
    """embedding 为 None 时直接返回 store."""
    action, merged_id = dedup_filter(None, [])
    assert action == "store"
    assert merged_id is None
    print("PASS test_dedup_filter_no_embedding_returns_store")


def test_dedup_filter_empty_existing_returns_store():
    """existing_records 为空时直接返回 store."""
    action, merged_id = dedup_filter([1.0, 0.0], [])
    assert action == "store"
    assert merged_id is None
    print("PASS test_dedup_filter_empty_existing_returns_store")


def test_dedup_filter_picks_highest_similarity():
    """多条记录时选相似度最高的那条。"""
    emb = [1.0, 0.0]
    existing = [
        _make_record(
            experience_id="low",
            lessons="low sim",
            embedding=[0.0, 1.0],  # cosine 0.0
        ),
        _make_record(
            experience_id="high",
            lessons="high sim",
            embedding=[0.99, 0.01],  # cosine ~0.99
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    # highest is ~0.99, which is >= 0.95 => skip
    assert action == "skip", f"expected skip, got {action}"
    assert merged_id == "high"
    print("PASS test_dedup_filter_picks_highest_similarity")


def test_dedup_filter_skips_records_without_embedding():
    """跳过无 embedding 的记录。"""
    emb = [1.0, 0.0]
    existing = [
        _make_record(
            experience_id="no_emb",
            lessons="no embedding",
            embedding=None,
        ),
    ]
    action, merged_id = dedup_filter(emb, existing)
    assert action == "store", f"expected store, got {action}"
    assert merged_id is None
    print("PASS test_dedup_filter_skips_records_without_embedding")


# ------------------------------------------------------------------------- #
# Integration tests: store() pipeline with dedup_filter
# ------------------------------------------------------------------------- #


def _setup_store() -> InMemoryExperienceStore:
    _setup()
    config = MemoryGovernanceConfig(quality_filter_enabled=True, min_content_length=1)
    return InMemoryExperienceStore(governance_config=config)


def test_store_pipeline_dedup_skip():
    """store() with identical embedding -> skip (no new record)."""
    import asyncio

    store = _setup_store()

    async def run():
        # First record stored normally
        r1 = build_experience_record(
            tenant_id="t1",
            goal="test goal",
            plan=_make_plan("test goal"),
            outcome="success",
            lessons="first lesson",
            embedding=[1.0, 0.0],
        )
        await store.store(r1)

        # Second record with same embedding -> skip
        r2 = build_experience_record(
            tenant_id="t1",
            goal="test goal 2",
            plan=_make_plan("test goal 2"),
            outcome="success",
            lessons="second lesson",
            embedding=[1.0, 0.0],
        )
        await store.store(r2)

        # r1 should exist
        got1 = await store.get(r1.experience_id)
        assert got1 is not None, "first record should be stored"
        assert got1.lessons == "first lesson"

        # r2 should NOT exist (skipped by dedup)
        got2 = await store.get(r2.experience_id)
        assert got2 is None, "skipped record should not be stored"

    asyncio.run(run())
    print("PASS test_store_pipeline_dedup_skip")


def test_store_pipeline_dedup_merge():
    """store() with merge_lessons -> lessons appended to existing record."""
    import asyncio

    store = _setup_store()

    async def run():
        r1 = build_experience_record(
            tenant_id="t1",
            goal="test goal",
            plan=_make_plan("test goal"),
            outcome="success",
            lessons="original lesson",
            embedding=[1.0, 0.0],
        )
        await store.store(r1)

        # Store a record with embedding that yields ~0.87 similarity -> merge_lessons
        r2 = build_experience_record(
            tenant_id="t1",
            goal="test goal 2",
            plan=_make_plan("test goal 2"),
            outcome="success",
            lessons="new lesson content",
            embedding=[0.87, 0.49],
        )
        await store.store(r2)

        # r1 should have both lessons, r2 should not be stored
        got1 = await store.get(r1.experience_id)
        assert got1 is not None, "first record should exist"
        assert "original lesson" in got1.lessons
        assert "new lesson content" in got1.lessons

        got2 = await store.get(r2.experience_id)
        assert got2 is None, "merged record should not be stored directly"

        # Check access_count was updated on merge target
        assert got1.access_count > 1, "access_count should be incremented on merge"

    asyncio.run(run())
    print("PASS test_store_pipeline_dedup_merge")


def test_store_pipeline_dedup_store():
    """store() with low similarity -> normal store."""
    import asyncio

    store = _setup_store()

    async def run():
        r1 = build_experience_record(
            tenant_id="t1",
            goal="test goal",
            plan=_make_plan("test goal"),
            outcome="success",
            lessons="first lesson",
            embedding=[1.0, 0.0],
        )
        await store.store(r1)

        # Orthogonal embedding -> store normally
        r2 = build_experience_record(
            tenant_id="t1",
            goal="test goal 2",
            plan=_make_plan("test goal 2"),
            outcome="success",
            lessons="different lesson",
            embedding=[0.0, 1.0],
        )
        await store.store(r2)

        # Both should exist
        got1 = await store.get(r1.experience_id)
        got2 = await store.get(r2.experience_id)
        assert got1 is not None
        assert got2 is not None
        assert got1.lessons == "first lesson"
        assert got2.lessons == "different lesson"

    asyncio.run(run())
    print("PASS test_store_pipeline_dedup_store")


def test_store_pipeline_metrics_dedup_skipped():
    """store() records dedup_skipped metric on skip."""
    import asyncio

    store = _setup_store()

    async def run():
        r1 = build_experience_record(
            tenant_id="t1",
            goal="test goal",
            plan=_make_plan("test goal"),
            outcome="success",
            lessons="first",
            embedding=[1.0, 0.0],
        )
        await store.store(r1)

        r2 = build_experience_record(
            tenant_id="t1",
            goal="test goal 2",
            plan=_make_plan("test goal 2"),
            outcome="success",
            lessons="second",
            embedding=[1.0, 0.0],
        )
        await store.store(r2)

        prom = get_experience_metrics().prometheus_text()
        assert "experience_dedup_skipped_total" in prom

    asyncio.run(run())
    print("PASS test_store_pipeline_metrics_dedup_skipped")


def test_store_pipeline_metrics_dedup_merged():
    """store() records dedup_merged metric on merge."""
    import asyncio

    store = _setup_store()

    async def run():
        r1 = build_experience_record(
            tenant_id="t1",
            goal="test goal",
            plan=_make_plan("test goal"),
            outcome="success",
            lessons="first",
            embedding=[1.0, 0.0],
        )
        await store.store(r1)

        r2 = build_experience_record(
            tenant_id="t1",
            goal="test goal 2",
            plan=_make_plan("test goal 2"),
            outcome="success",
            lessons="second",
            embedding=[0.87, 0.49],
        )
        await store.store(r2)

        prom = get_experience_metrics().prometheus_text()
        assert "experience_dedup_merged_total" in prom

    asyncio.run(run())
    print("PASS test_store_pipeline_metrics_dedup_merged")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_dedup_filter_skip_identical_vectors,
        test_dedup_filter_skip_high_similarity,
        test_dedup_filter_merge_lessons,
        test_dedup_filter_store_low_similarity,
        test_dedup_filter_no_embedding_returns_store,
        test_dedup_filter_empty_existing_returns_store,
        test_dedup_filter_picks_highest_similarity,
        test_dedup_filter_skips_records_without_embedding,
        test_store_pipeline_dedup_skip,
        test_store_pipeline_dedup_merge,
        test_store_pipeline_dedup_store,
        test_store_pipeline_metrics_dedup_skipped,
        test_store_pipeline_metrics_dedup_merged,
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
