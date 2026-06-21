#!/usr/bin/env python3
"""长记忆模块单元测试 — Phase F #31

运行：
    python3 tests/test_memory.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.memory import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryRecord,
    get_memory_metrics,
)
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import (  # noqa: E402
    _cosine_similarity,
    _gen_id,
    reset_memory_store_for_tests,
)


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()


def test_cosine_similarity():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert _cosine_similarity(a, b) == 1.0
    c = [0.0, 1.0, 0.0]
    assert _cosine_similarity(a, c) == 0.0
    d = [1.0, 1.0, 0.0]
    sim = _cosine_similarity(a, d)
    assert 0.6 < sim < 0.8
    assert _cosine_similarity([], []) == 0.0
    assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    print("PASS test_cosine_similarity")


def test_gen_id_unique():
    ids = {_gen_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(s.startswith("mem-") for s in ids)
    print("PASS test_gen_id_unique")


def test_record_expiry():
    r = MemoryRecord(
        memory_id="m1",
        tenant_id="t1",
        scope="session",
        scope_id="s1",
        content="hello",
    )
    assert not r.is_expired()
    r.expires_at = time.time() - 1
    assert r.is_expired()
    print("PASS test_record_expiry")


def test_inmemory_add_get():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        r = MemoryRecord(
            memory_id="m1",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="用户偏好：喜欢简洁回答",
        )
        mid = await store.add(r)
        assert mid == "m1"
        got = await store.get("m1")
        assert got is not None
        assert got.content == "用户偏好：喜欢简洁回答"
        assert got.tenant_id == "t1"
        # 不存在的 ID
        assert await store.get("non-existent") is None

    asyncio.run(run())
    print("PASS test_inmemory_add_get")


def test_inmemory_search_keyword():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="用户偏好：喜欢简洁回答",
            )
        )
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="历史话题：RAG 管道设计",
            )
        )
        # 子串命中
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="偏好",
            top_k=5,
        )
        assert len(results) == 1
        assert results[0].memory_id == "m1"
        # 分词命中
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="RAG 管道",
            top_k=5,
        )
        assert len(results) == 1
        assert results[0].memory_id == "m2"
        # 无命中
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="完全无关的查询",
            top_k=5,
        )
        assert len(results) == 0

    asyncio.run(run())
    print("PASS test_inmemory_search_keyword")


def test_inmemory_search_semantic():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="用户偏好",
                embedding=[1.0, 0.0, 0.0],
            )
        )
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="历史话题",
                embedding=[0.0, 1.0, 0.0],
            )
        )
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="query",
            top_k=1,
            query_embedding=[1.0, 0.0, 0.0],
        )
        assert len(results) == 1
        assert results[0].memory_id == "m1"

    asyncio.run(run())
    print("PASS test_inmemory_search_semantic")


def test_inmemory_scope_isolation():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="user1 记忆",
            )
        )
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="user",
                scope_id="u2",
                content="user2 记忆",
            )
        )
        # 不同 scope_id 互不可见
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="记忆",
            top_k=10,
        )
        assert len(results) == 1
        assert results[0].scope_id == "u1"
        # 不同 scope 互不可见
        await store.add(
            MemoryRecord(
                memory_id="m3",
                tenant_id="t1",
                scope="tenant",
                scope_id="t1",
                content="tenant 记忆",
            )
        )
        results = await store.search(
            tenant_id="t1",
            scope="tenant",
            scope_id="t1",
            query="记忆",
            top_k=10,
        )
        assert len(results) == 1
        assert results[0].scope == "tenant"

    asyncio.run(run())
    print("PASS test_inmemory_scope_isolation")


def test_inmemory_tenant_isolation():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="t1 记忆",
            )
        )
        results = await store.search(
            tenant_id="t2",
            scope="user",
            scope_id="u1",
            query="记忆",
            top_k=10,
        )
        assert len(results) == 0

    asyncio.run(run())
    print("PASS test_inmemory_tenant_isolation")


def test_inmemory_delete():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="hello",
            )
        )
        ok = await store.delete("m1")
        assert ok is True
        assert await store.get("m1") is None
        ok = await store.delete("non-existent")
        assert ok is False

    asyncio.run(run())
    print("PASS test_inmemory_delete")


def test_inmemory_list_by_scope():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        for i in range(5):
            await store.add(
                MemoryRecord(
                    memory_id=f"m{i}",
                    tenant_id="t1",
                    scope="user",
                    scope_id="u1",
                    content=f"记忆 {i}",
                )
            )
        records = await store.list_by_scope(
            tenant_id="t1", scope="user", scope_id="u1", limit=10
        )
        assert len(records) == 5
        # limit
        records = await store.list_by_scope(
            tenant_id="t1", scope="user", scope_id="u1", limit=2
        )
        assert len(records) == 2

    asyncio.run(run())
    print("PASS test_inmemory_list_by_scope")


def test_inmemory_expired_filtered():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="过期记忆",
                expires_at=time.time() - 1,
            )
        )
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="有效记忆",
            )
        )
        # get 应跳过过期
        assert await store.get("m1") is None
        assert await store.get("m2") is not None
        # search 应跳过过期
        results = await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="记忆",
            top_k=10,
        )
        assert len(results) == 1
        assert results[0].memory_id == "m2"

    asyncio.run(run())
    print("PASS test_inmemory_expired_filtered")


def test_metrics_recorded():
    _setup()
    store = InMemoryMemoryStore()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="hello",
            )
        )
        await store.search(
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            query="hello",
            top_k=5,
        )

    asyncio.run(run())
    m = get_memory_metrics()
    prom = m.prometheus_text()
    assert "memory_adds_total" in prom
    assert "memory_searches_total" in prom
    assert 'tenant_id="t1"' in prom
    assert 'scope="user"' in prom
    print("PASS test_metrics_recorded")


def main() -> int:
    tests = [
        test_cosine_similarity,
        test_gen_id_unique,
        test_record_expiry,
        test_inmemory_add_get,
        test_inmemory_search_keyword,
        test_inmemory_search_semantic,
        test_inmemory_scope_isolation,
        test_inmemory_tenant_isolation,
        test_inmemory_delete,
        test_inmemory_list_by_scope,
        test_inmemory_expired_filtered,
        test_metrics_recorded,
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
