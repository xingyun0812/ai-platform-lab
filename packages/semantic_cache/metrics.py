"""语义缓存指标（命中率 / 延迟 / 字节数）。"""

from __future__ import annotations

import threading
from collections import defaultdict


class SemanticCacheMetrics:
    """进程内 metrics，供 /metrics 暴露 Prometheus 文本。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._misses: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._store_errors: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._lookup_latency_ms: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
        self._tokens_saved: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._max_latency_samples = 2000

    def record_hit(self, *, tenant_id: str, model: str) -> None:
        key = (tenant_id or "unknown", model or "unknown")
        with self._lock:
            self._hits[key] += 1

    def record_miss(self, *, tenant_id: str, model: str) -> None:
        key = (tenant_id or "unknown", model or "unknown")
        with self._lock:
            self._misses[key] += 1

    def record_store_error(self, *, tenant_id: str, model: str) -> None:
        key = (tenant_id or "unknown", model or "unknown")
        with self._lock:
            self._store_errors[key] += 1

    def record_lookup_latency(
        self, *, tenant_id: str, model: str, latency_ms: float
    ) -> None:
        key = (tenant_id or "unknown", model or "unknown")
        with self._lock:
            bucket = self._lookup_latency_ms[key]
            bucket.append(float(latency_ms))
            if len(bucket) > self._max_latency_samples:
                del bucket[: len(bucket) - self._max_latency_samples]

    def record_tokens_saved(
        self, *, tenant_id: str, model: str, tokens: int
    ) -> None:
        if tokens <= 0:
            return
        key = (tenant_id or "unknown", model or "unknown")
        with self._lock:
            self._tokens_saved[key] += int(tokens)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "hits": dict(self._hits),
                "misses": dict(self._misses),
                "store_errors": dict(self._store_errors),
                "tokens_saved": dict(self._tokens_saved),
                "lookup_latency_ms": {k: list(v) for k, v in self._lookup_latency_ms.items()},
            }

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, int(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        lines.append("# HELP semantic_cache_hits_total Cache hits by tenant/model")
        lines.append("# TYPE semantic_cache_hits_total counter")
        for (tenant, model), count in sorted(snap["hits"].items()):  # type: ignore[union-attr]
            lines.append(
                f'semantic_cache_hits_total{{tenant_id="{tenant}",model="{model}"}} {count}'
            )
        lines.append("# HELP semantic_cache_misses_total Cache misses by tenant/model")
        lines.append("# TYPE semantic_cache_misses_total counter")
        for (tenant, model), count in sorted(snap["misses"].items()):  # type: ignore[union-attr]
            lines.append(
                f'semantic_cache_misses_total{{tenant_id="{tenant}",model="{model}"}} {count}'
            )
        lines.append("# HELP semantic_cache_store_errors_total Store errors by tenant/model")
        lines.append("# TYPE semantic_cache_store_errors_total counter")
        for (tenant, model), count in sorted(snap["store_errors"].items()):  # type: ignore[union-attr]
            lines.append(
                f'semantic_cache_store_errors_total{{tenant_id="{tenant}",model="{model}"}} {count}'
            )
        lines.append("# HELP semantic_cache_tokens_saved_total Tokens saved by tenant/model")
        lines.append("# TYPE semantic_cache_tokens_saved_total counter")
        for (tenant, model), count in sorted(snap["tokens_saved"].items()):  # type: ignore[union-attr]
            lines.append(
                f'semantic_cache_tokens_saved_total{{tenant_id="{tenant}",model="{model}"}} {count}'
            )
        lines.append("# HELP semantic_cache_lookup_latency_ms_p95 P95 lookup latency in ms")
        lines.append("# TYPE semantic_cache_lookup_latency_ms_p95 gauge")
        for (tenant, model), samples in sorted(snap["lookup_latency_ms"].items()):  # type: ignore[union-attr]
            p95 = self._p95(samples)
            lines.append(
                f'semantic_cache_lookup_latency_ms_p95{{tenant_id="{tenant}",model="{model}"}} {p95:.2f}'
            )
        return "\n".join(lines) + "\n"


_store: SemanticCacheMetrics | None = None


def get_semantic_cache_metrics() -> SemanticCacheMetrics:
    global _store
    if _store is None:
        _store = SemanticCacheMetrics()
    return _store


def reset_metrics_for_tests() -> None:
    global _store
    _store = None
