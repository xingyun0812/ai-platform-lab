"""tests/test_memory_governance_worker.py — Governance Worker CLI 单元测试 (Issue #219 X5).

Run:
    python3 tests/test_memory_governance_worker.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.memory.archive import InMemoryArchiveStore  # noqa: E402
from packages.memory.config import MemoryGovernanceConfig  # noqa: E402
from packages.memory.governance.purge import PurgeReport  # noqa: E402
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryRecord,
)


def _setup():
    reset_metrics_for_tests()


def _make_store(min_content_length: int = 1) -> InMemoryMemoryStore:
    return InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(min_content_length=min_content_length)
    )


# ------------------------------------------------------------------------- #
# Test Worker Logic Directly (CLI simulation via function calls)
# ------------------------------------------------------------------------- #


def test_worker_dry_run_shows_what_would_be_done():
    """Dry run shows what would be done without modifying."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(
        purge_enabled=True,
        purge_min_weight=0.5,
        purge_low_weight_days=1,
    )

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await run_purge_with_dry_run(store, archive, config)
        assert report.low_weight_archived == 1  # reports it
        assert report.archived == 0  # but doesn't count as archived
        # Verify nothing was actually modified
        got = await store.get("m1")
        assert got is not None

    asyncio.run(run())
    print("PASS test_worker_dry_run_shows_what_would_be_done")


def test_worker_force_actually_executes():
    """Force flag (dry_run=False) actually executes."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(
        purge_enabled=True,
        purge_min_weight=0.5,
        purge_low_weight_days=1,
    )

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await run_purge_force(store, archive, config)
        assert report.low_weight_deleted == 1
        got = await store.get("m1")
        assert got is None

    asyncio.run(run())
    print("PASS test_worker_force_actually_executes")


# We import the real run_purge — wrap it for clarity
from packages.memory.governance.purge import run_purge as _real_run_purge  # noqa: E402


async def run_purge_with_dry_run(
    store,
    archive,
    config,
    *,
    dry_run=True,
):
    return await _real_run_purge(store, archive, config, dry_run=dry_run)


async def run_purge_force(
    store,
    archive,
    config,
):
    return await _real_run_purge(store, archive, config, dry_run=False)


def test_worker_stats_returns_numbers():
    """Stats returns expected numeric fields."""
    _setup()
    store = _make_store()

    async def run():
        # Add some records to generate metrics
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test 1",
            )
        )
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test 2",
            )
        )

    asyncio.run(run())

    from packages.memory.governance.purge import get_governance_stats

    stats = get_governance_stats(store)
    assert stats["store_available"] is True
    assert isinstance(stats.get("purge_counts"), dict)
    assert isinstance(stats.get("library_totals"), dict)
    print("PASS test_worker_stats_returns_numbers")


def test_worker_handles_empty_store():
    """Worker handles empty store gracefully."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(purge_enabled=True)

    async def run():
        report = await _real_run_purge(store, archive, config)
        assert report.total_deleted == 0
        assert report.total_archived == 0
        assert len(report.errors) == 0

    asyncio.run(run())
    print("PASS test_worker_handles_empty_store")


def test_worker_handles_no_archive_store():
    """Worker handles archive_store=None (skip archiving)."""
    _setup()
    store = _make_store()
    config = MemoryGovernanceConfig(
        purge_enabled=True,
        purge_min_weight=0.5,
        purge_low_weight_days=1,
    )

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await _real_run_purge(store, None, config)
        assert report.low_weight_deleted == 1
        assert report.archived == 0  # no archive store, so nothing archived

    asyncio.run(run())
    print("PASS test_worker_handles_no_archive_store")


def test_worker_report_format():
    """PurgeReport items() returns correct format."""
    report = PurgeReport(
        expired_deleted=2,
        low_weight_archived=1,
        low_weight_deleted=1,
        duration_seconds=1.23,
    )
    items = report.items()
    assert isinstance(items, list)
    for name, val in items:
        assert isinstance(name, str)
        assert isinstance(val, int)
    assert len(items) == 7
    print("PASS test_worker_report_format")


def test_worker_no_purge_when_disabled():
    """Worker returns empty report when purge disabled."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(purge_enabled=False)

    async def run():
        report = await _real_run_purge(store, archive, config)
        assert report.total_deleted == 0

    asyncio.run(run())
    print("PASS test_worker_no_purge_when_disabled")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_worker_dry_run_shows_what_would_be_done,
        test_worker_force_actually_executes,
        test_worker_stats_returns_numbers,
        test_worker_handles_empty_store,
        test_worker_handles_no_archive_store,
        test_worker_report_format,
        test_worker_no_purge_when_disabled,
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
