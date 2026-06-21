#!/usr/bin/env python3
"""语义缓存单元测试（Phase G #34）

运行：
    python3 -m pytest tests/test_semantic_cache.py -v
或：
    python3 tests/test_semantic_cache.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.semantic_cache import (  # noqa: E402
    CacheLookupResult,
    InMemorySemanticCache,
    SemanticCacheConfig,
    build_cache_key,
    get_semantic_cache_metrics,
    normalize_messages,
)
from packages.semantic_cache.metrics import reset_metrics_for_tests  # noqa: E402
from packages.semantic_cache.store import (  # noqa: E402
    cosine_similarity,
    reset_semantic_cache_for_tests,
)


def _setup():
    reset_metrics_for_tests()
    reset_semantic_cache_for_tests()


def test_normalize_messages():
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "  你好  "},
    ]
    norm = normalize_messages(msgs)
    assert "system:你是助手" in norm
    assert "user:你好" in norm
    print("PASS test_normalize_messages")


def test_build_cache_key_deterministic():
    norm = "user:hi"
    k1 = build_cache_key(tenant_id="t1", model="m1", normalized=norm)
    k2 = build_cache_key(tenant_id="t1", model="m1", normalized=norm)
    assert k1 == k2, "相同输入应产生相同 key"
    k3 = build_cache_key(tenant_id="t2", model="m1", normalized=norm)
    assert k1 != k3, "不同租户应产生不同 key"
    print("PASS test_build_cache_key_deterministic")


def test_cosine_similarity():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, b) == 1.0
    c = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, c) == 0.0
    d = [1.0, 1.0, 0.0]
    sim = cosine_similarity(a, d)
    assert 0.6 < sim < 0.8, f"expected ~0.707, got {sim}"
    print("PASS test_cosine_similarity")


def test_exact_mode_hit_miss():
    _setup()
    cfg = SemanticCacheConfig(
        enabled=True,
        mode="exact",
        similarity_threshold=0.9,
        ttl_seconds=60,
        max_entries_per_tenant=8,
    )
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hello"}]

    async def run():
        # 未命中
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=False
        )
        assert r is None, f"expected miss, got {r}"
        # 写入
        await cache.store(
            tenant_id="t1",
            model="m1",
            messages=msgs,
            response={"choices": [{"message": {"content": "hi"}}]},
            usage_tokens=15,
            temperature=0.0,
            stream=False,
        )
        # 命中
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=False
        )
        assert isinstance(r, CacheLookupResult), f"expected hit, got {r}"
        assert r.mode == "exact"
        assert r.similarity == 1.0
        # 不同消息未命中
        r = await cache.lookup(
            tenant_id="t1",
            model="m1",
            messages=[{"role": "user", "content": "different"}],
            temperature=0.0,
            stream=False,
        )
        assert r is None

    asyncio.run(run())
    # metrics 校验
    m = get_semantic_cache_metrics()
    snap = m.snapshot()
    assert snap["hits"][("t1", "m1")] == 1, f"hits: {snap['hits']}"
    assert snap["misses"][("t1", "m1")] == 2, f"misses: {snap['misses']}"
    assert snap["tokens_saved"][("t1", "m1")] == 15
    print("PASS test_exact_mode_hit_miss")


def test_skip_stream():
    _setup()
    cfg = SemanticCacheConfig(enabled=True, mode="exact")
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hi"}]

    async def run():
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=True
        )
        assert isinstance(r, str), f"expected skip reason str, got {r}"
        assert "stream" in r

    asyncio.run(run())
    print("PASS test_skip_stream")


def test_skip_high_temperature():
    _setup()
    cfg = SemanticCacheConfig(
        enabled=True, mode="exact", max_temperature=0.3
    )
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hi"}]

    async def run():
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.7, stream=False
        )
        assert isinstance(r, str), f"expected skip reason str, got {r}"
        assert "temperature" in r

    asyncio.run(run())
    print("PASS test_skip_high_temperature")


def test_skip_model_in_blocklist():
    _setup()
    cfg = SemanticCacheConfig(
        enabled=True, mode="exact", skip_models=["o1-preview"]
    )
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hi"}]

    async def run():
        r = await cache.lookup(
            tenant_id="t1", model="o1-preview", messages=msgs, temperature=0.0, stream=False
        )
        assert isinstance(r, str), f"expected skip reason str, got {r}"
        assert "skip_list" in r

    asyncio.run(run())
    print("PASS test_skip_model_in_blocklist")


def test_ttl_expiry():
    _setup()
    cfg = SemanticCacheConfig(
        enabled=True, mode="exact", ttl_seconds=1, max_entries_per_tenant=8
    )
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hi"}]

    async def run():
        await cache.store(
            tenant_id="t1",
            model="m1",
            messages=msgs,
            response={"choices": []},
            usage_tokens=0,
            temperature=0.0,
            stream=False,
        )
        # 立即查应命中
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=False
        )
        assert isinstance(r, CacheLookupResult)
        # 等 TTL 过期
        time.sleep(1.2)
        r = await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=False
        )
        assert r is None, f"expected miss after TTL, got {r}"

    asyncio.run(run())
    print("PASS test_ttl_expiry")


def test_lru_eviction():
    _setup()
    cfg = SemanticCacheConfig(
        enabled=True, mode="exact", max_entries_per_tenant=3, ttl_seconds=60
    )
    cache = InMemorySemanticCache(cfg)

    async def run():
        for i in range(5):
            msgs = [{"role": "user", "content": f"q{i}"}]
            await cache.store(
                tenant_id="t1",
                model="m1",
                messages=msgs,
                response={"i": i},
                usage_tokens=0,
                temperature=0.0,
                stream=False,
            )
        # 仅最近 3 条应保留
        r = await cache.lookup(
            tenant_id="t1",
            model="m1",
            messages=[{"role": "user", "content": "q0"}],
            temperature=0.0,
            stream=False,
        )
        assert r is None, "q0 should have been evicted"
        r = await cache.lookup(
            tenant_id="t1",
            model="m1",
            messages=[{"role": "user", "content": "q4"}],
            temperature=0.0,
            stream=False,
        )
        assert isinstance(r, CacheLookupResult), "q4 should still be cached"

    asyncio.run(run())
    print("PASS test_lru_eviction")


def test_tenant_isolation():
    _setup()
    cfg = SemanticCacheConfig(enabled=True, mode="exact")
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "shared query"}]

    async def run():
        await cache.store(
            tenant_id="t1",
            model="m1",
            messages=msgs,
            response={"from": "t1"},
            usage_tokens=0,
            temperature=0.0,
            stream=False,
        )
        r = await cache.lookup(
            tenant_id="t2", model="m1", messages=msgs, temperature=0.0, stream=False
        )
        assert r is None, "t2 must not see t1's cache"

    asyncio.run(run())
    print("PASS test_tenant_isolation")


def test_prometheus_metrics_format():
    _setup()
    cfg = SemanticCacheConfig(enabled=True, mode="exact")
    cache = InMemorySemanticCache(cfg)
    msgs = [{"role": "user", "content": "hi"}]

    async def run():
        await cache.store(
            tenant_id="t1",
            model="m1",
            messages=msgs,
            response={"x": 1},
            usage_tokens=10,
            temperature=0.0,
            stream=False,
        )
        await cache.lookup(
            tenant_id="t1", model="m1", messages=msgs, temperature=0.0, stream=False
        )

    asyncio.run(run())
    out = get_semantic_cache_metrics().prometheus_text()
    assert "semantic_cache_hits_total" in out
    assert "semantic_cache_misses_total" in out
    assert "semantic_cache_tokens_saved_total" in out
    assert 'tenant_id="t1"' in out
    assert 'model="m1"' in out
    print("PASS test_prometheus_metrics_format")


def main() -> int:
    tests = [
        test_normalize_messages,
        test_build_cache_key_deterministic,
        test_cosine_similarity,
        test_exact_mode_hit_miss,
        test_skip_stream,
        test_skip_high_temperature,
        test_skip_model_in_blocklist,
        test_ttl_expiry,
        test_lru_eviction,
        test_tenant_isolation,
        test_prometheus_metrics_format,
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
