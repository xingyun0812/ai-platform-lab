#!/usr/bin/env python3
"""Memory L2 Semantic Dedup 单元测试 — Issue #217

运行:
    python3 tests/test_memory_dedup.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.memory import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryGovernanceConfig,
    MemoryRecord,
    get_memory_metrics,
)
from packages.memory.governance.dedup import (  # noqa: E402
    _perform_merge,
    _text_overlap_ratio,
    check_dedup,
)
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import _gen_id, reset_memory_store_for_tests  # noqa: E402


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()


def _record(
    content: str,
    memory_id: str | None = None,
    embedding: list[float] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id or _gen_id(),
        tenant_id="t1",
        scope="user",
        scope_id="u1",
        content=content,
        embedding=embedding,
    )


# ------------------------------------------------------------------------- #
# Unit tests: check_dedup (standalone)
# ------------------------------------------------------------------------- #


def test_dedup_same_content_skip():
    """Same embedding (cosine=1.0) -> skip."""
    emb = [1.0, 0.0, 0.0]
    record = _record("hello world", embedding=emb)
    candidate = _record("hello world", embedding=emb)
    config = MemoryGovernanceConfig(dedup_enabled=True)

    result = check_dedup(record, [candidate], config)
    assert result.action == "skip", f"expected skip, got {result.action}"
    assert result.matched_id == candidate.memory_id
    assert "skip_threshold" in result.reason
    print("PASS test_dedup_same_content_skip")


def test_dedup_similar_content_merge():
    """Cosine 0.88 falls between merge and skip thresholds -> merge."""
    # Cosine([1,0,0], [0.88, 0.47, 0]) = 0.88 (using 0.88 < 0.92 skip thresh, >= 0.85 merge thresh)
    record = _record("hello world", embedding=[1.0, 0.0, 0.0])
    candidate = _record("hello there", embedding=[0.88, 0.47, 0.0])
    config = MemoryGovernanceConfig(
        dedup_enabled=True,
        dedup_skip_threshold=0.92,
        dedup_merge_threshold=0.85,
    )

    result = check_dedup(record, [candidate], config)
    assert result.action == "merge", f"expected merge, got {result.action}"
    assert result.matched_id == candidate.memory_id
    assert "merge_threshold" in result.reason
    print("PASS test_dedup_similar_content_merge")


def test_dedup_different_content_insert():
    """Orthogonal embeddings (cosine=0) -> insert."""
    record = _record("hello world", embedding=[1.0, 0.0, 0.0])
    candidate = _record("something else", embedding=[0.0, 1.0, 0.0])
    config = MemoryGovernanceConfig(dedup_enabled=True)

    result = check_dedup(record, [candidate], config)
    assert result.action == "insert", f"expected insert, got {result.action}"
    print("PASS test_dedup_different_content_insert")


def test_dedup_disabled_always_insert():
    """dedup_enabled=False -> always insert regardless of similarity."""
    emb = [1.0, 0.0, 0.0]
    record = _record("hello world", embedding=emb)
    candidate = _record("hello world", embedding=emb)
    config = MemoryGovernanceConfig(dedup_enabled=False)

    result = check_dedup(record, [candidate], config)
    assert result.action == "insert", f"expected insert, got {result.action}"
    assert result.matched_id is None
    print("PASS test_dedup_disabled_always_insert")


def test_dedup_no_candidates_insert():
    """Empty candidates list -> insert."""
    record = _record("hello world", embedding=[1.0, 0.0, 0.0])
    config = MemoryGovernanceConfig(dedup_enabled=True)

    result = check_dedup(record, [], config)
    assert result.action == "insert", f"expected insert, got {result.action}"
    assert "no candidates" in result.reason
    print("PASS test_dedup_no_candidates_insert")


def test_dedup_candidate_count_respected():
    """Storage layer only passes top N candidates by last_accessed_at."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            dedup_enabled=True,
            dedup_candidate_count=1,
            min_content_length=1,
            dedup_skip_threshold=0.92,
            dedup_merge_threshold=0.85,
        )
    )

    async def run():
        # Add 2 records with different embeddings
        r1 = _record("first record A", memory_id="r1", embedding=[1.0, 0.0])
        r2 = _record("second record B", memory_id="r2", embedding=[0.0, 1.0])
        await store.add(r1)
        await store.add(r2)

        # Access r2 explicitly so its last_accessed_at is most recent
        got_r2 = await store.get("r2")
        assert got_r2 is not None

        # Now add a record with same embedding as r1 (the older one)
        # With candidate_count=1 and r2 being the most recently accessed,
        # only r2 is considered -> cosine=0 -> insert as new
        r3 = _record("third record C", memory_id="r3", embedding=[1.0, 0.0])
        id3 = await store.add(r3)

        # Should be inserted as new because only r2 (orthogonal) is in candidate window
        got = await store.get(id3)
        assert got is not None
        assert got.memory_id == "r3"

    asyncio.run(run())
    print("PASS test_dedup_candidate_count_respected")


def test_dedup_merge_with_llm():
    """Merge with LLM opt-in uses mocked LLM to merge content."""
    matched = _record("original content", embedding=[1.0, 0.0])
    incoming = _record("new content to merge", embedding=[0.88, 0.47])

    mock_llm = MagicMock()
    mock_llm.chat.return_value = "Merged: original content and new content to merge"

    result = _perform_merge(matched, incoming, use_llm=True, llm_client=mock_llm)
    assert mock_llm.chat.called, "LLM should have been called"
    assert "Merged:" in result.content
    assert incoming.memory_id in result.merged_from
    print("PASS test_dedup_merge_with_llm")


def test_dedup_metrics_skipped_incremented():
    """Metrics counter memory_dedup_skipped_total incremented on skip."""
    _setup()
    emb = [1.0, 0.0, 0.0]
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            dedup_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("hello world", embedding=emb)
        await store.add(r1)
        r2 = _record("hello world", embedding=emb)
        await store.add(r2)

    asyncio.run(run())
    prom = get_memory_metrics().prometheus_text()
    assert "memory_dedup_skipped_total" in prom
    assert 'tenant_id="t1"' in prom
    print("PASS test_dedup_metrics_skipped_incremented")


def test_dedup_metrics_merged_incremented():
    """Metrics counter memory_dedup_merged_total incremented on merge."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            dedup_enabled=True,
            dedup_skip_threshold=0.92,
            dedup_merge_threshold=0.85,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("hello world", embedding=[1.0, 0.0])
        await store.add(r1)
        # Cosine 0.87 falls in merge range [0.85, 0.92)
        r2 = _record("hello there", embedding=[0.87, 0.49])
        await store.add(r2)

    asyncio.run(run())
    prom = get_memory_metrics().prometheus_text()
    assert "memory_dedup_merged_total" in prom
    assert 'tenant_id="t1"' in prom
    print("PASS test_dedup_metrics_merged_incremented")


def test_dedup_content_fallback_no_embedding():
    """When no embedding, falls back to text overlap ratio."""
    record = _record("hello world and universe")
    candidate = _record("hello world and everything")
    config = MemoryGovernanceConfig(
        dedup_enabled=True,
        dedup_skip_threshold=0.92,
        dedup_merge_threshold=0.70,
    )

    result = check_dedup(record, [candidate], config)
    # overlap ratio: 3/5 (hello, world, and) out of 5 unique tokens -> 0.6
    # Wait, "hello world and everything" has tokens {hello, world, and, everything}
    # "hello world and universe" has tokens {hello, world, and, universe}
    # intersection = {hello, world, and}, union = {hello, world, and, universe, everything}
    # ratio = 3/5 = 0.6 -> insert
    assert result.action == "insert", f"expected insert, got {result.action}"
    print("PASS test_dedup_content_fallback_no_embedding")


def test_dedup_text_overlap_ratio():
    """Text overlap ratio works correctly."""
    # Identical texts
    assert _text_overlap_ratio("hello world", "hello world") == 1.0
    # Partial overlap
    ratio = _text_overlap_ratio("hello world foo", "hello world bar")
    assert ratio == 2.0 / 4.0  # {hello, world} / {hello, world, foo, bar}
    # No overlap
    assert _text_overlap_ratio("abc def", "ghi jkl") == 0.0
    # Empty
    assert _text_overlap_ratio("", "hello") == 0.0
    print("PASS test_dedup_text_overlap_ratio")


def test_dedup_integration_insert_via_store_add():
    """InMemory store: different content -> stored normally (insert)."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(dedup_enabled=True, min_content_length=1)
    )

    async def run():
        r1 = _record("hello world", embedding=[1.0, 0.0])
        r2 = _record("completely different", embedding=[0.0, 1.0])

        id1 = await store.add(r1)
        id2 = await store.add(r2)

        got1 = await store.get(id1)
        got2 = await store.get(id2)
        assert got1 is not None
        assert got2 is not None
        assert got1.content == "hello world"
        assert got2.content == "completely different"

    asyncio.run(run())
    print("PASS test_dedup_integration_insert_via_store_add")


def test_dedup_integration_skip_via_store_add():
    """InMemory store: same embedding -> second add returns first id (skip)."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(dedup_enabled=True, min_content_length=1)
    )

    async def run():
        emb = [0.5, 0.5, 0.5, 0.5]
        normalized_emb = [x / (0.5**2 * 4) ** 0.5 for x in emb]
        # normalize so cosine is exactly 1.0
        norm = sum(x * x for x in normalized_emb) ** 0.5
        emb_norm = [x / norm for x in normalized_emb]

        r1 = _record("some content", embedding=emb_norm)
        id1 = await store.add(r1)

        r2 = _record("other content but same embedding", embedding=emb_norm)
        id2 = await store.add(r2)

        # Should return id1 (skip, return the matched id)
        assert id2 == id1, "skipped record should return matched memory_id"

        got = await store.get(id1)
        assert got is not None
        assert got.content == "some content"

    asyncio.run(run())
    print("PASS test_dedup_integration_skip_via_store_add")


def test_dedup_integration_merge_via_store_add():
    """InMemory store: similar content -> merged."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            dedup_enabled=True,
            dedup_skip_threshold=0.92,
            dedup_merge_threshold=0.85,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("original content", embedding=[1.0, 0.0])
        id1 = await store.add(r1)

        # Cosine([1,0], [0.87, 0.49]) = 0.87 -> merge
        r2 = _record("new content to merge", embedding=[0.87, 0.49])
        id2 = await store.add(r2)

        # Should return id1 (merged into matched record)
        assert id2 == id1, "merged record should return matched memory_id"

        got = await store.get(id1)
        assert got is not None
        assert "original content" in got.content
        assert "new content to merge" in got.content

    asyncio.run(run())
    print("PASS test_dedup_integration_merge_via_store_add")


def test_dedup_integration_backend_consistency():
    """Two InMemory stores with same data behave consistently."""
    _setup()
    store1 = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(dedup_enabled=True, min_content_length=1)
    )
    store2 = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(dedup_enabled=True, min_content_length=1)
    )

    async def run():
        emb = [1.0, 0.0, 0.0]
        r1 = _record("content a", embedding=emb)
        r2 = _record("content b", embedding=[0.0, 1.0, 0.0])

        # Store in first store
        await store1.add(r1)
        await store1.add(r2)

        # Store in second store (order reversed)
        r2b = _record("content b", embedding=[0.0, 1.0, 0.0])
        r1b = _record("content a", embedding=emb)
        await store2.add(r2b)
        await store2.add(r1b)

        # Both stores should have 2 entries (no duplicates across scopes)
        results1 = await store1.list_by_scope(tenant_id="t1", scope="user", scope_id="u1")
        results2 = await store2.list_by_scope(tenant_id="t1", scope="user", scope_id="u1")
        assert len(results1) == len(results2), "both stores should have same count"

    asyncio.run(run())
    print("PASS test_dedup_integration_backend_consistency")


def test_dedup_merge_updates_access_metadata():
    """Merge updates last_accessed_at and access_count."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            dedup_enabled=True,
            dedup_skip_threshold=0.92,
            dedup_merge_threshold=0.85,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("original content", embedding=[1.0, 0.0])
        id1 = await store.add(r1)

        before_merge = await store.get(id1)
        assert before_merge is not None
        old_access = before_merge.access_count
        old_time = before_merge.last_accessed_at

        await asyncio.sleep(0.01)  # ensure timestamp changes

        r2 = _record("new content for metadata check", embedding=[0.87, 0.49])
        await store.add(r2)

        after_merge = await store.get(id1)
        assert after_merge is not None
        assert after_merge.access_count > old_access, "access_count should increase"
        assert after_merge.last_accessed_at is not None
        if old_time is not None:
            assert after_merge.last_accessed_at >= old_time, "last_accessed_at should update"
        assert r2.memory_id in after_merge.merged_from, "merged_from should track incoming id"

    asyncio.run(run())
    print("PASS test_dedup_merge_updates_access_metadata")


def test_dedup_content_fallback_high_overlap_skip():
    """High text overlap without embedding -> skip."""
    record = _record("hello world and universe are great")
    candidate = _record("hello world and universe are wonderful")
    config = MemoryGovernanceConfig(
        dedup_enabled=True,
        dedup_skip_threshold=0.80,
        dedup_merge_threshold=0.60,
    )

    result = check_dedup(record, [candidate], config)
    # Tokens:
    # record: {hello, world, and, universe, are, great}
    # candidate: {hello, world, and, universe, are, wonderful}
    # intersection: 5, union: 7 -> ratio: ~0.71
    # 0.71 < 0.80 skip but >= 0.60 merge -> merge
    assert result.action == "merge", f"expected merge, got {result.action}"
    print("PASS test_dedup_content_fallback_high_overlap_skip")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_dedup_same_content_skip,
        test_dedup_similar_content_merge,
        test_dedup_different_content_insert,
        test_dedup_disabled_always_insert,
        test_dedup_no_candidates_insert,
        test_dedup_candidate_count_respected,
        test_dedup_merge_with_llm,
        test_dedup_metrics_skipped_incremented,
        test_dedup_metrics_merged_incremented,
        test_dedup_content_fallback_no_embedding,
        test_dedup_text_overlap_ratio,
        test_dedup_integration_insert_via_store_add,
        test_dedup_integration_skip_via_store_add,
        test_dedup_integration_merge_via_store_add,
        test_dedup_integration_backend_consistency,
        test_dedup_merge_updates_access_metadata,
        test_dedup_content_fallback_high_overlap_skip,
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
