"""长记忆指标（写入/检索/缓存命中率）。"""

from __future__ import annotations

import threading
from collections import defaultdict


class MemoryMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._adds: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._searches: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._cache_hits: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._cache_misses: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._store_errors: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._search_latency_ms: defaultdict[tuple[str, str], list[float]] = defaultdict(list)

    def record_add(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._adds[key] += 1

    def record_search(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._searches[key] += 1

    def record_cache_hit(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._cache_hits[key] += 1

    def record_cache_miss(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._cache_misses[key] += 1

    def record_store_error(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._store_errors[key] += 1

    def record_search_latency(
        self, *, tenant_id: str, scope: str, latency_ms: float
    ) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            bucket = self._search_latency_ms[key]
            bucket.append(float(latency_ms))
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, int(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    def prometheus_text(self) -> str:
        with self._lock:
            adds = dict(self._adds)
            searches = dict(self._searches)
            cache_hits = dict(self._cache_hits)
            cache_misses = dict(self._cache_misses)
            errors = dict(self._store_errors)
            latencies = {k: list(v) for k, v in self._search_latency_ms.items()}
        lines: list[str] = []
        lines.append("# HELP memory_adds_total Memory records added by tenant/scope")
        lines.append("# TYPE memory_adds_total counter")
        for (t, s), c in sorted(adds.items()):
            lines.append(f'memory_adds_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_searches_total Memory searches by tenant/scope")
        lines.append("# TYPE memory_searches_total counter")
        for (t, s), c in sorted(searches.items()):
            lines.append(f'memory_searches_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_cache_hits_total Memory cache hits")
        lines.append("# TYPE memory_cache_hits_total counter")
        for (t, s), c in sorted(cache_hits.items()):
            lines.append(f'memory_cache_hits_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_cache_misses_total Memory cache misses")
        lines.append("# TYPE memory_cache_misses_total counter")
        for (t, s), c in sorted(cache_misses.items()):
            lines.append(f'memory_cache_misses_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_store_errors_total Memory store errors")
        lines.append("# TYPE memory_store_errors_total counter")
        for (t, s), c in sorted(errors.items()):
            lines.append(f'memory_store_errors_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_search_latency_ms_p95 P95 search latency")
        lines.append("# TYPE memory_search_latency_ms_p95 gauge")
        for (t, s), samples in sorted(latencies.items()):
            lines.append(
                f'memory_search_latency_ms_p95{{tenant_id="{t}",scope="{s}"}} {self._p95(samples):.2f}'
            )
        return "\n".join(lines) + "\n"


_store: MemoryMetrics | None = None


def get_memory_metrics() -> MemoryMetrics:
    global _store
    if _store is None:
        _store = MemoryMetrics()
    return _store


def reset_metrics_for_tests() -> None:
    global _store
    _store = None
