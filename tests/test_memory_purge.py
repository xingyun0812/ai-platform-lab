"""tests/test_memory_purge.py — Purge & Archive 单元测试 (Issue #219 X5).

Run:
    python3 tests/test_memory_purge.py
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
from packages.memory.governance.purge import (  # noqa: E402
    PurgeReport,
    get_governance_stats,
    run_purge,
)
from packages.memory.metrics import reset_metrics_for_tests  # noqa: E402
from packages.memory.store import (  # noqa: E402
    InMemoryMemoryStore,
    MemoryRecord,
)


def _setup():
    reset_metrics_for_tests()


def _make_store(min_content_length: int = 1) -> InMemoryMemoryStore:
    """Create a permissive InMemoryMemoryStore for testing (classifier disabled)."""
    return InMemoryMemoryStore(
        governance_config=MemoryGovernanceConfig(
            min_content_length=min_content_length, classifier_enabled=False
        )
    )


# ------------------------------------------------------------------------- #
# Test PurgeReport
# ------------------------------------------------------------------------- #


def test_purge_report_properties():
    """PurgeReport computed properties work correctly."""
    r = PurgeReport(
        expired_deleted=3,
        low_weight_deleted=2,
        zero_access_deleted=1,
        orphaned_deleted=0,
        low_weight_archived=2,
        zero_access_archived=1,
    )
    assert r.total_deleted == 6  # 3 + 2 + 1 + 0
    assert r.total_archived == 3  # 2 + 1
    assert len(r.items()) == 7
    print("PASS test_purge_report_properties")


# ------------------------------------------------------------------------- #
# Test run_purge with InMemoryMemoryStore (should be no-op)
# ------------------------------------------------------------------------- #


def test_purge_inmemory_noop():
    """InMemoryMemoryStore: purge with empty store returns empty report."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(purge_enabled=True)

    async def run():
        report = await run_purge(store, archive, config)
        assert report.total_deleted == 0
        assert report.expired_deleted == 0
        assert len(report.errors) == 0

    asyncio.run(run())
    print("PASS test_purge_inmemory_noop")


# ------------------------------------------------------------------------- #
# Test purge with InMemoryMemoryStore list_expired
# ------------------------------------------------------------------------- #


def test_expired_record_deleted_no_archive():
    """Expired record deleted directly without archive."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(purge_enabled=True, purge_min_weight=0.1)

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="expired memory",
                expires_at=time.time() - 100,
            )
        )
        report = await run_purge(store, archive, config)
        assert report.expired_deleted == 1
        assert report.total_deleted == 1
        assert report.archived == 0
        # Verify deleted from store
        got = await store.get("m1")
        assert got is None

    asyncio.run(run())
    print("PASS test_expired_record_deleted_no_archive")


def test_low_weight_old_record_archived_then_deleted():
    """Low-weight + old record archived then deleted."""
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
                content="low weight memory",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await run_purge(store, archive, config)
        assert report.low_weight_archived == 1
        assert report.low_weight_deleted == 1
        assert report.archived == 1
        # Verify deleted from store
        got = await store.get("m1")
        assert got is None
        # Verify archived
        archived = await archive.list_archived(tenant_id="t1", scope="user", scope_id="u1")
        assert len(archived) == 1
        assert archived[0].purge_reason == "low_weight"
        assert archived[0].memory_id == "m1"

    asyncio.run(run())
    print("PASS test_low_weight_old_record_archived_then_deleted")


def test_zero_access_old_record_archived_then_deleted():
    """Zero-access + old record archived then deleted."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(
        purge_enabled=True,
        purge_min_weight=0.5,
        purge_zero_access_days=1,
    )

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="zero access memory",
                weight=1.0,  # not low weight
                access_count=0,
                created_at=time.time() - 86400 * 10,  # old enough
            )
        )
        report = await run_purge(store, archive, config)
        assert report.zero_access_archived == 1
        assert report.zero_access_deleted == 1
        assert report.archived == 1
        got = await store.get("m1")
        assert got is None
        archived = await archive.list_archived(tenant_id="t1", scope="user", scope_id="u1")
        assert len(archived) == 1
        assert archived[0].purge_reason == "zero_access"

    asyncio.run(run())
    print("PASS test_zero_access_old_record_archived_then_deleted")


def test_archive_record_contains_purge_reason():
    """Archived record metadata includes purge_reason."""
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
                content="low weight memory",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await run_purge(store, archive, config)
        assert report.low_weight_deleted == 1

        archived = await archive.list_archived(tenant_id="t1", scope="user", scope_id="u1")
        assert len(archived) == 1
        a = archived[0]
        assert a.purge_reason == "low_weight"
        assert a.original_weight == 0.1
        assert a.content == "low weight memory"
        assert a.memory_id == "m1"

    asyncio.run(run())
    print("PASS test_archive_record_contains_purge_reason")


def test_dry_run_no_deletes():
    """Dry run does not delete anything."""
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
                content="to delete",
                weight=0.1,
                last_accessed_at=time.time() - 86400 * 10,
            )
        )
        report = await run_purge(store, archive, config, dry_run=True)
        assert report.low_weight_archived == 1  # would be archived
        assert report.low_weight_deleted == 1  # would be deleted
        # But actually not deleted
        got = await store.get("m1")
        assert got is not None
        # And not archived
        archived = await archive.list_archived(tenant_id="t1", scope="user", scope_id="u1")
        assert len(archived) == 0

    asyncio.run(run())
    print("PASS test_dry_run_no_deletes")


def test_purge_disabled_by_config():
    """Purge returns empty report when purge_enabled=False."""
    _setup()
    store = _make_store()
    archive = InMemoryArchiveStore()
    config = MemoryGovernanceConfig(purge_enabled=False)

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test",
                expires_at=time.time() - 100,
            )
        )
        report = await run_purge(store, archive, config)
        assert report.total_deleted == 0
        assert report.expired_deleted == 0

    asyncio.run(run())
    print("PASS test_purge_disabled_by_config")


def test_governance_stats_with_store():
    """get_governance_stats returns expected keys."""
    _setup()
    store = _make_store()

    async def run():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="user",
                scope_id="u1",
                content="test memory",
            )
        )

    asyncio.run(run())
    stats = get_governance_stats(store)
    assert stats["store_available"] is True
    assert stats["store_type"] == "InMemoryMemoryStore"
    assert "purge_counts" in stats
    assert "archived_count" in stats
    assert "library_totals" in stats
    print("PASS test_governance_stats_with_store")


def test_governance_stats_no_store():
    """get_governance_stats with None store returns empty stats."""
    stats = get_governance_stats(None)
    assert stats["store_available"] is False
    assert stats["store_type"] == "none"
    print("PASS test_governance_stats_no_store")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_purge_report_properties,
        test_purge_inmemory_noop,
        test_expired_record_deleted_no_archive,
        test_low_weight_old_record_archived_then_deleted,
        test_zero_access_old_record_archived_then_deleted,
        test_archive_record_contains_purge_reason,
        test_dry_run_no_deletes,
        test_purge_disabled_by_config,
        test_governance_stats_with_store,
        test_governance_stats_no_store,
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
