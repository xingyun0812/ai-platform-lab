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
        self._quality_rejected: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._search_latency_ms: defaultdict[tuple[str, str], list[float]] = defaultdict(list)

        # — Governance metrics —
        self._dedup_skipped: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._dedup_merged: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._verify_check: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._verify_demoted: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._verify_latency_ms: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
        self._purge: defaultdict[str, int] = defaultdict(int)  # reason -> count
        self._archived: int = 0
        self._governance_run_duration: float = 0.0
        self._library_total: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._library_expired: defaultdict[tuple[str, str], int] = defaultdict(int)

        # — Classifier metrics —
        self._classifier_classified: dict = defaultdict(int)  # key is "class|source"
        self._classifier_latency: dict = defaultdict(list)  # key is source -> list of ms
        self._classifier_llm_calls: int = 0
        self._classifier_llm_errors: int = 0
        self._classifier_rule_matched: dict = defaultdict(int)  # key is pattern

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

    def record_quality_rejected(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._quality_rejected[key] += 1

    def record_search_latency(self, *, tenant_id: str, scope: str, latency_ms: float) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            bucket = self._search_latency_ms[key]
            bucket.append(float(latency_ms))
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    # --- Governance metric recorders ---

    def record_dedup_skipped(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._dedup_skipped[key] += 1

    def record_dedup_merged(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._dedup_merged[key] += 1

    def record_verify_check(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._verify_check[key] += 1

    def record_verify_demoted(self, *, tenant_id: str, scope: str) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._verify_demoted[key] += 1

    def record_verify_latency(self, *, tenant_id: str, scope: str, latency_ms: float) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            bucket = self._verify_latency_ms[key]
            bucket.append(float(latency_ms))
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    def record_purge(self, *, reason: str) -> None:
        with self._lock:
            self._purge[reason or "unknown"] += 1

    def record_archive(self) -> None:
        with self._lock:
            self._archived += 1

    def record_governance_run(self, *, duration_seconds: float) -> None:
        with self._lock:
            self._governance_run_duration = float(duration_seconds)

    def record_library_total(self, *, tenant_id: str, scope: str, count: int) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._library_total[key] = count

    def record_library_expired(self, *, tenant_id: str, scope: str, count: int) -> None:
        key = (tenant_id or "unknown", scope or "unknown")
        with self._lock:
            self._library_expired[key] = count

    # --- Classifier metric recorders ---

    def record_classified(self, *, class_label: str, source: str) -> None:
        """memory_classified_total{class,source}"""
        key = f"{class_label}|{source}"
        with self._lock:
            self._classifier_classified[key] += 1

    def record_classifier_latency(self, *, source: str, latency_ms: float) -> None:
        """memory_classifier_latency_ms{source}"""
        with self._lock:
            bucket = self._classifier_latency[source or "unknown"]
            bucket.append(float(latency_ms))
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    def record_classifier_llm_calls(self) -> None:
        """memory_classifier_llm_calls"""
        with self._lock:
            self._classifier_llm_calls += 1

    def record_classifier_llm_error(self) -> None:
        """memory_classifier_llm_errors"""
        with self._lock:
            self._classifier_llm_errors += 1

    def record_classifier_rule_matched(self, *, pattern: str) -> None:
        """memory_classifier_rule_matched{pattern}"""
        with self._lock:
            self._classifier_rule_matched[pattern or "unknown"] += 1

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
            quality_rejected = dict(self._quality_rejected)
            latencies = {k: list(v) for k, v in self._search_latency_ms.items()}
            dedup_skipped = dict(self._dedup_skipped)
            dedup_merged = dict(self._dedup_merged)
            verify_check = dict(self._verify_check)
            verify_demoted = dict(self._verify_demoted)
            verify_lat = {k: list(v) for k, v in self._verify_latency_ms.items()}
            purge_counts = dict(self._purge)
            archived = self._archived
            gov_run_dur = self._governance_run_duration
            lib_total = dict(self._library_total)
            lib_expired = dict(self._library_expired)
            cls_classified = dict(self._classifier_classified)
            cls_latency = {k: list(v) for k, v in self._classifier_latency.items()}
            cls_llm_calls = self._classifier_llm_calls
            cls_llm_errors = self._classifier_llm_errors
            cls_rule_matched = dict(self._classifier_rule_matched)
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
        lines.append("# HELP memory_quality_rejected_total Memory quality filter rejections")
        lines.append("# TYPE memory_quality_rejected_total counter")
        for (t, s), c in sorted(quality_rejected.items()):
            lines.append(f'memory_quality_rejected_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_search_latency_ms_p95 P95 search latency")
        lines.append("# TYPE memory_search_latency_ms_p95 gauge")
        for (t, s), samples in sorted(latencies.items()):
            lines.append(
                f'memory_search_latency_ms_p95{{tenant_id="{t}",scope="{s}"}} {self._p95(samples):.2f}'
            )

        # --- Governance metrics ---
        lines.append("# HELP memory_dedup_skipped_total Records skipped by semantic dedup")
        lines.append("# TYPE memory_dedup_skipped_total counter")
        for (t, s), c in sorted(dedup_skipped.items()):
            lines.append(f'memory_dedup_skipped_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_dedup_merged_total Records merged by semantic dedup")
        lines.append("# TYPE memory_dedup_merged_total counter")
        for (t, s), c in sorted(dedup_merged.items()):
            lines.append(f'memory_dedup_merged_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_verify_check_total Recall verification checks")
        lines.append("# TYPE memory_verify_check_total counter")
        for (t, s), c in sorted(verify_check.items()):
            lines.append(f'memory_verify_check_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_verify_demoted_total Top-1 demoted by recall verification")
        lines.append("# TYPE memory_verify_demoted_total counter")
        for (t, s), c in sorted(verify_demoted.items()):
            lines.append(f'memory_verify_demoted_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_verify_latency_ms Recall verification LLM latency")
        lines.append("# TYPE memory_verify_latency_ms gauge")
        for (t, s), samples in sorted(verify_lat.items()):
            p95 = self._p95(samples)
            lines.append(f'memory_verify_latency_ms_p95{{tenant_id="{t}",scope="{s}"}} {p95:.2f}')
        lines.append("# HELP governance_purge_total Records purged by governance worker")
        lines.append("# TYPE governance_purge_total counter")
        for reason, c in sorted(purge_counts.items()):
            lines.append(f'governance_purge_total{{reason="{reason}"}} {c}')
        lines.append("# HELP governance_archived_total Records archived before purge")
        lines.append("# TYPE governance_archived_total counter")
        lines.append(f"governance_archived_total {archived}")
        lines.append("# HELP governance_runtime_seconds Last governance run duration")
        lines.append("# TYPE governance_runtime_seconds gauge")
        lines.append(f"governance_runtime_seconds {gov_run_dur}")
        lines.append("# HELP memory_library_total Active memory count by tenant/scope")
        lines.append("# TYPE memory_library_total gauge")
        for (t, s), c in sorted(lib_total.items()):
            lines.append(f'memory_library_total{{tenant_id="{t}",scope="{s}"}} {c}')
        lines.append("# HELP memory_library_expired Expired-but-not-yet-purged count")
        lines.append("# TYPE memory_library_expired gauge")
        for (t, s), c in sorted(lib_expired.items()):
            lines.append(f'memory_library_expired{{tenant_id="{t}",scope="{s}"}} {c}')

        # --- Classifier metrics ---
        lines.append("# HELP memory_classified_total Memory records classified by label/source")
        lines.append("# TYPE memory_classified_total counter")
        for key, c in sorted(cls_classified.items()):
            # key is "class_label|source"
            parts = key.split("|", 1)
            cl = parts[0]
            src = parts[1] if len(parts) > 1 else "unknown"
            lines.append(f'memory_classified_total{{class="{cl}",source="{src}"}} {c}')
        lines.append("# HELP memory_classifier_latency_ms Classifier latency P95 by source")
        lines.append("# TYPE memory_classifier_latency_ms gauge")
        for src, samples in sorted(cls_latency.items()):
            p95 = self._p95(samples)
            lines.append(f'memory_classifier_latency_ms_p95{{source="{src}"}} {p95:.2f}')
        lines.append("# HELP memory_classifier_llm_calls_total Classifier LLM calls")
        lines.append("# TYPE memory_classifier_llm_calls_total counter")
        lines.append(f"memory_classifier_llm_calls_total {cls_llm_calls}")
        lines.append("# HELP memory_classifier_llm_errors_total Classifier LLM errors")
        lines.append("# TYPE memory_classifier_llm_errors_total counter")
        lines.append(f"memory_classifier_llm_errors_total {cls_llm_errors}")
        lines.append(
            "# HELP memory_classifier_rule_matched_total Classifier rule matches by pattern"
        )
        lines.append("# TYPE memory_classifier_rule_matched_total counter")
        for pattern, c in sorted(cls_rule_matched.items()):
            lines.append(f'memory_classifier_rule_matched_total{{pattern="{pattern}"}} {c}')

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
