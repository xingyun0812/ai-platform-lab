"""记忆治理后台 Worker — CLI 入口。

Usage:
    python -m packages.memory.governance_worker run [--dry-run] [--force]
    python -m packages.memory.governance_worker stats
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from packages.memory.archive import init_archive_store
from packages.memory.config import MemoryGovernanceConfig
from packages.memory.governance.purge import get_governance_stats, run_purge
from packages.memory.store import get_memory_store, init_memory_store

logger = logging.getLogger("ai_platform.memory.governance_worker")


def _init_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _get_store_and_config() -> tuple:
    """Get or initialize the memory store and config."""
    store = get_memory_store()
    if store is None:
        init_memory_store()
        store = get_memory_store()
    if store is None:
        print("Error: memory store not available", file=sys.stderr)
        sys.exit(1)
    config = MemoryGovernanceConfig()
    return store, config


async def _cmd_run(args: argparse.Namespace) -> int:
    store, config = _get_store_and_config()

    archive_store = None
    if config.archive_enabled and not args.dry_run:
        archive_store = init_archive_store()

    print(
        f"Governance run: dry_run={args.dry_run}, "
        f"force={args.force}, "
        f"purge_enabled={config.purge_enabled}, "
        f"archive_enabled={config.archive_enabled}"
    )

    if args.dry_run:
        print("[DRY RUN] No records will be modified")

    t0 = time.perf_counter()
    report = await run_purge(
        store,
        archive_store=archive_store,
        config=config,
        dry_run=args.dry_run,
    )
    elapsed = time.perf_counter() - t0

    print(f"\nPurge Report ({elapsed:.2f}s):")
    print(f"  Expired deleted:       {report.expired_deleted}")
    print(f"  Low-weight archived:   {report.low_weight_archived}")
    print(f"  Low-weight deleted:    {report.low_weight_deleted}")
    print(f"  Zero-access archived:  {report.zero_access_archived}")
    print(f"  Zero-access deleted:   {report.zero_access_deleted}")
    print(f"  Orphaned deleted:      {report.orphaned_deleted}")
    print(f"  Total archived:        {report.archived}")
    print(f"  Total deleted:         {report.total_deleted}")
    if report.errors:
        print(f"  Errors ({len(report.errors)}):")
        for err in report.errors:
            print(f"    - {err}")

    return 0


async def _cmd_stats(args: argparse.Namespace) -> int:
    store = get_memory_store()
    if store is None:
        print("Memory store not available")
        return 0

    config = MemoryGovernanceConfig()

    # Use the stats function which parses from prometheus metrics
    stats = get_governance_stats(store)

    print("\nMemory Governance Stats:")
    print(f"  Store available:   {stats['store_available']}")
    print(f"  Store type:        {stats['store_type']}")
    print(f"  Archived count:    {stats.get('archived_count', 0)}")
    print(f"  Purge counts:      {stats.get('purge_counts', {})}")
    print(f"  Library totals:    {stats.get('library_totals', {})}")
    print(f"  Library expired:   {stats.get('library_expired', {})}")

    # Config summary
    print("\nConfig:")
    print(f"  purge_enabled:          {config.purge_enabled}")
    print(f"  purge_min_weight:       {config.purge_min_weight}")
    print(f"  purge_zero_access_days: {config.purge_zero_access_days}")
    print(f"  purge_low_weight_days:  {config.purge_low_weight_days}")
    print(f"  archive_enabled:        {config.archive_enabled}")
    print(f"  governance_cron:        {config.governance_cron}")

    return 0


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "run":
        return await _cmd_run(args)
    elif args.command == "stats":
        return await _cmd_stats(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


def main() -> int:
    _init_logging()
    parser = argparse.ArgumentParser("governance-worker")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Execute governance purge run")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without actually deleting"
    )
    run_parser.add_argument(
        "--force", action="store_true", help="Bypass safety checks (currently unused)"
    )

    sub.add_parser("stats", help="Show memory library health stats")

    args = parser.parse_args()
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
