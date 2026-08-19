#!/usr/bin/env python3
"""Memory X5c: Store integration of L0 classifier.

Tests that InMemoryMemoryStore.add() and PostgresMemoryStore.add()
correctly invoke the classifier during the add pipeline.

Run:
    python3 tests/test_memory_classifier.py
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
    MemoryGovernanceConfig,
    MemoryRecord,
    get_memory_metrics,
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
    scope: str = "user",
    expires_at: float | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id or _gen_id(),
        tenant_id="t1",
        scope=scope,
        scope_id="u1",
        content=content,
        embedding=embedding,
        expires_at=expires_at,
    )


# ------------------------------------------------------------------------- #
# L0 Classifier Store Integration Tests
# ------------------------------------------------------------------------- #


def _test_add_preference():
    """Preference content -> scope=user, metadata.class="preference"."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        r = _record("平时喜欢简洁风格，偏好快速回答方式呢")
        mid = await store.add(r)

        got = await store.get(mid)
        assert got is not None, "record should be stored"
        assert got.metadata.get("class") == "preference", (
            f"expected preference, got {got.metadata.get('class')}"
        )
        assert got.scope == "user", f"preference scope should be user, got {got.scope}"
        assert got.metadata.get("feedback_bonus") == 0.2

    asyncio.run(run())
    print("PASS test_add_preference")


def _test_add_factual():
    """Factual content -> metadata.class="factual", scope unchanged."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        r = _record("当前使用的 Python 版本为 3.11", scope="tenant")
        mid = await store.add(r)

        got = await store.get(mid)
        assert got is not None
        assert got.metadata.get("class") == "factual", (
            f"expected factual, got {got.metadata.get('class')}"
        )
        # factual keeps existing scope
        assert got.scope == "tenant", f"factual should keep scope tenant, got {got.scope}"
        assert "feedback_bonus" not in got.metadata

    asyncio.run(run())
    print("PASS test_add_factual")


def _test_add_ephemeral():
    """Ephemeral content -> scope=session."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            classifier_rule_enabled=False,  # force LLM path -> will use fallback "ephemeral"
            classifier_llm_model=None,
            classifier_llm_fallback_class="ephemeral",
            min_content_length=1,
        )
    )

    async def run():
        r = _record("今天天气不错，适合出去散步放松心情")
        mid = await store.add(r)

        got = await store.get(mid)
        assert got is not None
        assert got.metadata.get("class") == "ephemeral", (
            f"expected ephemeral, got {got.metadata.get('class')}"
        )
        assert got.scope == "session", f"ephemeral scope should be session, got {got.scope}"
        assert got.metadata.get("feedback_bonus") == -0.1
        assert got.expires_at is not None
        # expires_at should be ~86400 seconds from now
        assert abs(got.expires_at - time.time() - 86400) < 2.0

    asyncio.run(run())
    print("PASS test_add_ephemeral")


def _test_add_noise():
    """Noise content -> still returns memory_id but not stored."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        r = _record("好的")
        mid = await store.add(r)

        # Should return the memory_id
        assert mid is not None
        # But should NOT be retrievable (not stored)
        got = await store.get(mid)
        assert got is None, "noise record should not be stored"

    asyncio.run(run())
    print("PASS test_add_noise")


def _test_classifier_disabled():
    """classifier_enabled=False -> no classification metadata."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=False,
            min_content_length=1,
        )
    )

    async def run():
        r = _record("some ordinary content that might be classified")
        mid = await store.add(r)

        got = await store.get(mid)
        assert got is not None
        # No classifier metadata should be present
        assert "class" not in got.metadata, "classifier disabled should not set class"
        assert "class_confidence" not in got.metadata
        assert "class_source" not in got.metadata

    asyncio.run(run())
    print("PASS test_classifier_disabled")


def _test_add_with_embedding_preserved():
    """Classification doesn't lose embedding."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        emb = [0.1, 0.2, 0.3, 0.4]
        r = _record("平时喜欢简洁风格，偏好快速回答方式呢", embedding=emb)
        mid = await store.add(r)

        got = await store.get(mid)
        assert got is not None
        assert got.embedding == emb, "embedding should be preserved after classification"
        assert got.metadata.get("class") == "preference"

    asyncio.run(run())
    print("PASS test_add_with_embedding_preserved")


def _test_metrics_recorded():
    """Classifier metrics are recorded for classified records."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("平时喜欢简洁风格，偏好快速回答方式呢")  # preference
        r2 = _record("当前使用的 Python 版本为 3.11")  # factual
        r3 = _record("好的")  # noise -> rejected
        await store.add(r1)
        await store.add(r2)
        await store.add(r3)

    asyncio.run(run())

    prom = get_memory_metrics().prometheus_text()
    # Check classifier metrics exist
    assert "memory_classified_total" in prom, "should have classifier metrics"
    # Check quality_rejected reflects noise rejection
    assert "memory_quality_rejected_total" in prom

    print("PASS test_metrics_recorded")


def _test_rule_wins_before_llm():
    """Obvious noise keywords never reach LLM."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            classifier_rule_enabled=True,
            min_content_length=1,
        )
    )

    async def run():
        # These are obvious noise keywords from the rule patterns
        r = _record("hello")
        mid = await store.add(r)

        # noise rejected -> not stored
        got = await store.get(mid)
        assert got is None, "noise keyword should be rejected by rule classifier"

        # Check metrics show rule source (not LLM)
        prom = get_memory_metrics().prometheus_text()
        # The classified metric should have source=rule (not llm)
        assert 'source="rule"' in prom or 'class="noise"' in prom

    asyncio.run(run())
    print("PASS test_rule_wins_before_llm")


def _test_quality_filter_runs_first():
    """L1 quality_filter still runs before L0 classifier."""
    _setup()
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=50,  # very high threshold
        )
    )

    async def run():
        r = _record("short content")
        mid = await store.add(r)

        # Should be rejected by quality_filter before classifier
        got = await store.get(mid)
        assert got is None, "short content should be rejected by quality_filter"

    asyncio.run(run())
    print("PASS test_quality_filter_runs_first")


def _test_config_override_per_call():
    """Per-call governance_config overrides store-level config for classifier."""
    _setup()
    # Store-level: classifier disabled
    store = InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            classifier_enabled=False,
            min_content_length=1,
        )
    )

    async def run():
        r1 = _record("平时喜欢简洁风格，偏好快速回答方式呢")
        # Per-call: classifier enabled
        per_call_cfg = MemoryGovernanceConfig(
            classifier_enabled=True,
            min_content_length=1,
        )
        mid1 = await store.add(r1, governance_config=per_call_cfg)

        got1 = await store.get(mid1)
        assert got1 is not None
        # Should be classified because per-call config overrides
        assert got1.metadata.get("class") == "preference", (
            f"expected preference with per-call config, got {got1.metadata.get('class')}"
        )

        # Second record with store-level (disabled) config
        r2 = _record("平时喜欢简洁风格，偏好快速回答方式呢")
        mid2 = await store.add(r2)

        got2 = await store.get(mid2)
        assert got2 is not None
        # Should NOT have classifier metadata because store-level config disables it
        assert "class" not in got2.metadata, "store-level disabled config should not classify"

    asyncio.run(run())
    print("PASS test_config_override_per_call")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        _test_add_preference,
        _test_add_factual,
        _test_add_ephemeral,
        _test_add_noise,
        _test_classifier_disabled,
        _test_add_with_embedding_preserved,
        _test_metrics_recorded,
        _test_rule_wins_before_llm,
        _test_quality_filter_runs_first,
        _test_config_override_per_call,
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
