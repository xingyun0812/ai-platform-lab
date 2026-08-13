#!/usr/bin/env python3
"""Memory quality_filter 单元测试 — Issue #205

运行：
    python3 tests/test_memory_quality.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.memory import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryGovernanceConfig,
    MemoryRecord,
    get_memory_metrics,
    quality_filter,
)
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import reset_memory_store_for_tests  # noqa: E402


def _setup():
    reset_metrics_for_tests()
    reset_memory_store_for_tests()


# ------------------------------------------------------------------------- #
# Unit tests: quality_filter (standalone)
# ------------------------------------------------------------------------- #


def _record(content: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id="m1",
        tenant_id="t1",
        scope="user",
        scope_id="u1",
        content=content,
    )


def test_quality_filter_rejects_empty():
    """quality_filter() rejects empty strings (< min_content_length)."""
    passed, reason = quality_filter(_record(""))
    assert not passed, "expected reject for empty content"
    assert "too short" in reason
    print("PASS test_quality_filter_rejects_empty")


def test_quality_filter_rejects_short():
    """quality_filter() rejects strings shorter than min_content_length."""
    passed, reason = quality_filter(_record("hi"))
    assert not passed, "expected reject for short content"
    assert "too short" in reason
    print("PASS test_quality_filter_rejects_short")


def test_quality_filter_rejects_punctuation_only():
    """quality_filter() rejects pure punctuation/whitespace/numbers."""
    passed, reason = quality_filter(_record("!!! ??? ... 12345  !!"))
    assert not passed, "expected reject for punctuation-only"
    assert "no substance" in reason
    print("PASS test_quality_filter_rejects_punctuation_only")


def test_quality_filter_rejects_numbers_only():
    """quality_filter() rejects pure numbers."""
    passed, reason = quality_filter(_record("12345678901234567890"))
    assert not passed, "expected reject for numbers-only"
    assert "no substance" in reason
    print("PASS test_quality_filter_rejects_numbers_only")


def test_quality_filter_rejects_whitespace_only():
    """quality_filter() rejects whitespace-only content."""
    passed, reason = quality_filter(_record("   \t\n  \n  " + "x" * 10))
    # This has 'x' chars so length is ok, but might still be caught
    passed2, reason2 = quality_filter(_record("   \t\n  \n  "))
    assert not passed2, "expected reject for whitespace-only"
    assert "no substance" in reason2 or "too short" in reason2
    print("PASS test_quality_filter_rejects_whitespace_only")


def test_quality_filter_rejects_echo():
    """quality_filter() rejects content identical to input message."""
    passed, reason = quality_filter(
        _record("what is the capital of france"),
        input_message="what is the capital of france",
    )
    assert not passed, "expected reject for echo content"
    assert "echo guard" in reason
    print("PASS test_quality_filter_rejects_echo")


def test_quality_filter_passes_normal_content():
    """quality_filter() passes normal, substantive content."""
    passed, reason = quality_filter(_record("The capital of France is Paris."))
    assert passed, f"expected pass for normal content, got: {reason}"
    print("PASS test_quality_filter_passes_normal_content")


def test_quality_filter_passes_chinese_content():
    """quality_filter() passes non-ASCII substantive content."""
    passed, reason = quality_filter(_record("用户偏好：喜欢简洁回答，不喜欢冗长解释。"))
    assert passed, f"expected pass for Chinese content, got: {reason}"
    print("PASS test_quality_filter_passes_chinese_content")


def test_quality_filter_configurable_threshold():
    """quality_filter() respects custom min_content_length config."""
    config = MemoryGovernanceConfig(min_content_length=100)
    passed, reason = quality_filter(
        _record("short but ok under default"),
        config=config,
    )
    assert not passed, "expected reject with custom threshold"
    assert "too short" in reason

    # Also test with config disabled
    config_disabled = MemoryGovernanceConfig(quality_filter_enabled=False)
    passed2, reason2 = quality_filter(
        _record(""),
        config=config_disabled,
    )
    assert passed2, "expected pass when quality_filter disabled"
    print("PASS test_quality_filter_configurable_threshold")


def test_quality_filter_rejects_mixed_punctuation_and_numbers():
    """quality_filter() rejects content that is only punct/num/whitespace."""
    passed, reason = quality_filter(_record("!@#$% 12345 ^&*()  " + "x" * 20))
    # This has 'x' chars so it has substance — should pass
    assert passed, f"expected pass for mixed content, got: {reason}"

    # Now test with only punct/num/whitespace
    passed2, reason2 = quality_filter(_record("!@#$% 12345 ^&*()  "))
    # This is 18 chars, fails length check first
    assert not passed2
    print("PASS test_quality_filter_rejects_mixed_punctuation_and_numbers")


# ------------------------------------------------------------------------- #
# Integration tests: MemoryStore.add() with quality_filter
# ------------------------------------------------------------------------- #


def _setup_store() -> InMemoryMemoryStore:
    _setup()
    config = MemoryGovernanceConfig(min_content_length=20, quality_filter_enabled=True)
    return InMemoryMemoryStore(governance_config=config)


def test_store_add_rejects_short_content():
    """MemoryStore.add() rejects short content via quality_filter."""
    import asyncio

    store = _setup_store()

    async def run():
        r = MemoryRecord(
            memory_id="m1",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="short",
        )
        await store.add(r)
        # Should NOT be stored
        got = await store.get("m1")
        assert got is None, "short content should not be stored"

    asyncio.run(run())
    print("PASS test_store_add_rejects_short_content")


def test_store_add_rejects_echo():
    """MemoryStore.add() rejects echo content when input_message provided."""
    import asyncio

    store = _setup_store()

    async def run():
        r = MemoryRecord(
            memory_id="m2",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="What is the capital of France?",
        )
        await store.add(r, input_message="What is the capital of France?")
        got = await store.get("m2")
        assert got is None, "echo content should not be stored"

    asyncio.run(run())
    print("PASS test_store_add_rejects_echo")


def test_store_add_passes_normal_content():
    """MemoryStore.add() stores normal content."""
    import asyncio

    store = _setup_store()

    async def run():
        r = MemoryRecord(
            memory_id="m3",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="The capital of France is Paris.",
        )
        await store.add(r)
        got = await store.get("m3")
        assert got is not None, "normal content should be stored"
        assert got.content == "The capital of France is Paris."

    asyncio.run(run())
    print("PASS test_store_add_passes_normal_content")


def test_store_add_metrics_quality_rejected():
    """MemoryStore.add() increments quality_rejected metric on rejection."""
    import asyncio

    store = _setup_store()

    async def run():
        r = MemoryRecord(
            memory_id="m4",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="",
        )
        await store.add(r)
        prom = get_memory_metrics().prometheus_text()
        assert "memory_quality_rejected_total" in prom
        assert 'tenant_id="t1"' in prom

    asyncio.run(run())
    print("PASS test_store_add_metrics_quality_rejected")


def test_store_add_config_disabled():
    """MemoryStore.add() stores even low-quality content when quality_filter is disabled."""
    import asyncio

    _setup()
    config = MemoryGovernanceConfig(quality_filter_enabled=False)
    store = InMemoryMemoryStore(governance_config=config)

    async def run():
        r = MemoryRecord(
            memory_id="m5",
            tenant_id="t1",
            scope="user",
            scope_id="u1",
            content="",
        )
        await store.add(r)
        got = await store.get("m5")
        # Without quality_filter, empty content is stored
        assert got is not None, "content should be stored when quality_filter disabled"
        assert got.content == ""

    asyncio.run(run())
    print("PASS test_store_add_config_disabled")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_quality_filter_rejects_empty,
        test_quality_filter_rejects_short,
        test_quality_filter_rejects_punctuation_only,
        test_quality_filter_rejects_numbers_only,
        test_quality_filter_rejects_whitespace_only,
        test_quality_filter_rejects_echo,
        test_quality_filter_passes_normal_content,
        test_quality_filter_passes_chinese_content,
        test_quality_filter_configurable_threshold,
        test_quality_filter_rejects_mixed_punctuation_and_numbers,
        test_store_add_rejects_short_content,
        test_store_add_rejects_echo,
        test_store_add_passes_normal_content,
        test_store_add_metrics_quality_rejected,
        test_store_add_config_disabled,
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
