from __future__ import annotations

import math
import threading
import time
from collections import defaultdict


class MetricsStore:
    """进程内粗粒度指标（第 5 周实验用）。"""

    def __init__(self, *, max_latency_samples: int = 2000) -> None:
        self._lock = threading.Lock()
        self._max_samples = max_latency_samples
        self._request_total: defaultdict[tuple[str, str, str], int] = defaultdict(int)
        self._latency_ms: defaultdict[tuple[str, str], list[float]] = defaultdict(list)

    def record_request(
        self,
        *,
        path: str,
        tenant_id: str,
        status_code: int,
        latency_ms: float,
    ) -> None:
        tenant = tenant_id or "unknown"
        status = str(status_code)
        key_total = (path, tenant, status)
        key_lat = (path, tenant)
        with self._lock:
            self._request_total[key_total] += 1
            bucket = self._latency_ms[key_lat]
            bucket.append(latency_ms)
            if len(bucket) > self._max_samples:
                del bucket[: len(bucket) - self._max_samples]

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, math.ceil(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    def prometheus_text(self) -> str:
        lines: list[str] = []
        now = int(time.time())
        with self._lock:
            totals = dict(self._request_total)
            latencies = {k: list(v) for k, v in self._latency_ms.items()}

        lines.append("# HELP http_requests_total Total HTTP requests")
        lines.append("# TYPE http_requests_total counter")
        for (path, tenant, status), count in sorted(totals.items()):
            lines.append(
                f'http_requests_total{{path="{path}",tenant_id="{tenant}",status="{status}"}} {count}'
            )

        lines.append("# HELP http_request_duration_ms_p95 P95 latency in milliseconds")
        lines.append("# TYPE http_request_duration_ms_p95 gauge")
        for (path, tenant), samples in sorted(latencies.items()):
            p95 = self._p95(samples)
            lines.append(
                f'http_request_duration_ms_p95{{path="{path}",tenant_id="{tenant}"}} {p95:.2f}'
            )

        lines.append("# HELP metrics_generated_timestamp Unix timestamp")
        lines.append("# TYPE metrics_generated_timestamp gauge")
        lines.append(f"metrics_generated_timestamp {now}")
        return "\n".join(lines) + "\n"


_store: MetricsStore | None = None


def get_metrics_store() -> MetricsStore:
    global _store
    if _store is None:
        _store = MetricsStore()
    return _store
