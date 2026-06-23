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
        self._canary_auto_rollback_total: defaultdict[str, int] = defaultdict(int)

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

    def record_canary_auto_rollback(self, *, kb_id: str) -> None:
        with self._lock:
            self._canary_auto_rollback_total[kb_id or "unknown"] += 1

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, math.ceil(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    def dashboard_snapshot(self) -> dict[str, float | int | list[dict[str, float | int | str]]]:
        """Console Dashboard 用 JSON 快照。"""
        with self._lock:
            totals = dict(self._request_total)
            latencies = {k: list(v) for k, v in self._latency_ms.items()}

        total_requests = sum(totals.values())
        error_requests = sum(
            count for (_, _, status), count in totals.items() if str(status).startswith(("4", "5"))
        )
        error_rate = (error_requests / total_requests * 100.0) if total_requests else 0.0

        tenant_totals: dict[str, int] = {}
        for (_, tenant, _), count in totals.items():
            tenant_totals[tenant] = tenant_totals.get(tenant, 0) + count

        qps = round(total_requests / 60.0, 2) if total_requests else 0.0
        timeline: list[dict[str, float | int | str]] = []
        for hour in range(12):
            timeline.append(
                {
                    "time": f"{hour * 2:02d}:00",
                    "requests": max(0, total_requests // 12),
                    "errors": max(0, error_requests // 12),
                }
            )

        tokens_by_tenant = [
            {"tenant_id": tenant, "tokens": count * 100}
            for tenant, count in sorted(tenant_totals.items())
        ]

        return {
            "qps": qps,
            "qps_delta": 0.0,
            "tokens_today": sum(tenant_totals.values()) * 100,
            "error_rate": round(error_rate, 2),
            "active_sessions": len(tenant_totals),
            "requests_timeline": timeline,
            "tokens_by_tenant": tokens_by_tenant or [{"tenant_id": "admin", "tokens": 0}],
        }

    def prometheus_text(self) -> str:
        lines: list[str] = []
        now = int(time.time())
        with self._lock:
            totals = dict(self._request_total)
            latencies = {k: list(v) for k, v in self._latency_ms.items()}
            rollbacks = dict(self._canary_auto_rollback_total)

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

        lines.append("# HELP canary_auto_rollback_total Auto rollbacks triggered by canary guard")
        lines.append("# TYPE canary_auto_rollback_total counter")
        for kb_id, count in sorted(rollbacks.items()):
            lines.append(f'canary_auto_rollback_total{{kb_id="{kb_id}"}} {count}')

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
