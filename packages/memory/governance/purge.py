"""记忆清理归档 — Purge & Archive 治理后台任务。

Purge 规则（按优先级依次执行）：
1. 过期记录：expires_at < now() -> delete directly (no archive)
2. 低权重记录：weight < purge_min_weight AND last_accessed_at < now() - purge_low_weight_days -> archive + delete
3. 零访问记录：access_count = 0 AND created_at < now() - purge_zero_access_days -> archive + delete
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from packages.memory.archive import ArchiveStore
from packages.memory.config import MemoryGovernanceConfig
from packages.memory.metrics import get_memory_metrics
from packages.memory.store import MemoryStore

logger = logging.getLogger("ai_platform.memory.governance.purge")


@dataclass
class PurgeReport:
    """Purge 执行报告。"""

    expired_deleted: int = 0
    low_weight_archived: int = 0
    low_weight_deleted: int = 0
    zero_access_archived: int = 0
    zero_access_deleted: int = 0
    orphaned_deleted: int = 0
    archived: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_deleted(self) -> int:
        return (
            self.expired_deleted
            + self.low_weight_deleted
            + self.zero_access_deleted
            + self.orphaned_deleted
        )

    @property
    def total_archived(self) -> int:
        return self.low_weight_archived + self.zero_access_archived

    def items(self) -> list[tuple[str, int]]:
        return [
            ("expired_deleted", self.expired_deleted),
            ("low_weight_archived", self.low_weight_archived),
            ("low_weight_deleted", self.low_weight_deleted),
            ("zero_access_archived", self.zero_access_archived),
            ("zero_access_deleted", self.zero_access_deleted),
            ("orphaned_deleted", self.orphaned_deleted),
            ("archived", self.archived),
        ]


async def run_purge(
    store: MemoryStore,
    archive_store: ArchiveStore | None,
    config: MemoryGovernanceConfig,
    *,
    dry_run: bool = False,
) -> PurgeReport:
    """Execute purge governance task.

    Args:
        store: MemoryStore instance (InMemory returns empty report).
        archive_store: ArchiveStore instance (None skips archiving).
        config: Governance configuration.
        dry_run: True to only report, no actual deletes.

    Returns:
        PurgeReport: Execution report.
    """
    report = PurgeReport()
    metrics = get_memory_metrics()
    start = time.perf_counter()

    if not config.purge_enabled:
        logger.info("purge disabled by config")
        report.duration_seconds = time.perf_counter() - start
        return report

    # Only works with stores that support list_expired (Postgres etc.)
    try:
        expired = await store.list_expired(tenant_id="*", scope="*", scope_id="*")
    except (NotImplementedError, Exception) as e:
        logger.info("purge skipped: store does not support list_expired (%s)", e)
        report.duration_seconds = time.perf_counter() - start
        return report

    now = time.time()
    cutoff_low_weight = now - (config.purge_low_weight_days * 86400)
    cutoff_zero_access = now - (config.purge_zero_access_days * 86400)

    for record in expired:
        try:
            # Rule 1: Expired records -> delete directly
            if record.expires_at is not None and record.expires_at < now:
                if not dry_run:
                    await store.delete(record.memory_id)
                    metrics.record_purge(reason="expired")
                report.expired_deleted += 1
                continue

            # Rule 2: Low weight + old last_access -> archive + delete
            if (
                record.weight < config.purge_min_weight
                and record.last_accessed_at is not None
                and record.last_accessed_at < cutoff_low_weight
            ):
                if archive_store is not None and not dry_run:
                    await archive_store.archive(record, purge_reason="low_weight")
                    metrics.record_archive()
                    report.archived += 1
                report.low_weight_archived += 1
                if not dry_run:
                    await store.delete(record.memory_id)
                    metrics.record_purge(reason="low_weight")
                report.low_weight_deleted += 1
                continue

            # Rule 3: Zero access + old creation -> archive + delete
            if record.access_count == 0 and record.created_at < cutoff_zero_access:
                if archive_store is not None and not dry_run:
                    await archive_store.archive(record, purge_reason="zero_access")
                    metrics.record_archive()
                    report.archived += 1
                report.zero_access_archived += 1
                if not dry_run:
                    await store.delete(record.memory_id)
                    metrics.record_purge(reason="zero_access")
                report.zero_access_deleted += 1
                continue

        except Exception as e:
            msg = f"error processing {record.memory_id}: {e}"
            logger.warning(msg)
            report.errors.append(msg)

    if not dry_run:
        metrics.record_governance_run(duration_seconds=time.perf_counter() - start)

    report.duration_seconds = time.perf_counter() - start
    return report


def get_governance_stats(store: MemoryStore | None) -> dict[str, Any]:
    """Get memory library health stats."""
    stats: dict[str, Any] = {
        "store_available": store is not None,
        "store_type": type(store).__name__ if store else "none",
    }
    if store is None:
        return stats

    metrics = get_memory_metrics()
    prom = metrics.prometheus_text()

    import re

    purge_counts: dict[str, int] = {}
    for m in re.finditer(r'governance_purge_total\{reason="([^"]+)"} (\d+)', prom):
        purge_counts[m.group(1)] = int(m.group(2))
    stats["purge_counts"] = purge_counts

    arch_m = re.search(r"governance_archived_total (\d+)", prom)
    stats["archived_count"] = int(arch_m.group(1)) if arch_m else 0

    lib_totals: dict[str, int] = {}
    for m in re.finditer(r'memory_library_total\{tenant_id="([^"]+)",scope="([^"]+)"} (\d+)', prom):
        key = f"{m.group(1)}/{m.group(2)}"
        lib_totals[key] = int(m.group(3))
    stats["library_totals"] = lib_totals

    lib_expired: dict[str, int] = {}
    for m in re.finditer(
        r'memory_library_expired\{tenant_id="([^"]+)",scope="([^"]+)"} (\d+)', prom
    ):
        key = f"{m.group(1)}/{m.group(2)}"
        lib_expired[key] = int(m.group(3))
    stats["library_expired"] = lib_expired

    return stats
