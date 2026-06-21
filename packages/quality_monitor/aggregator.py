"""质量聚合器 — Phase J #48

滑动时间窗口聚合质量指标（满意度、差评率、均分等）。
可选 Redis 后端；回退到内存。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.quality_monitor.aggregator")


# ─────────────────────────── Dataclass ───────────────────────


@dataclass
class QualityMetric:
    tenant_id: str
    window_seconds: int
    total_requests: int
    thumbs_up: int
    thumbs_down: int
    avg_rating: float
    bad_case_count: int
    satisfaction_rate: float
    timestamp: float = field(default_factory=time.time)


# ─────────────────────────── Raw event ───────────────────────


@dataclass
class _RawEvent:
    feedback_type: str
    rating: int | None
    ts: float


# ─────────────────────────── Aggregator ──────────────────────


class QualityAggregator:
    """滑动窗口质量聚合（内存实现）。"""

    def __init__(self, window_seconds: int = 300) -> None:
        self._default_window = window_seconds
        self._lock = threading.RLock()
        # tenant_id → list of _RawEvent
        self._events: dict[str, list[_RawEvent]] = {}

    # ── write ──────────────────────────────────────────────

    async def record_request(
        self,
        tenant_id: str,
        feedback_type: str,
        rating: int | None = None,
    ) -> None:
        ev = _RawEvent(feedback_type=feedback_type, rating=rating, ts=time.time())
        with self._lock:
            self._events.setdefault(tenant_id, []).append(ev)
            # 限制最大保留量（最近 2000 条），避免内存无限增长
            if len(self._events[tenant_id]) > 2000:
                self._events[tenant_id] = self._events[tenant_id][-2000:]

    # ── read ───────────────────────────────────────────────

    async def get_current(
        self,
        tenant_id: str,
        window_seconds: int | None = None,
    ) -> QualityMetric:
        ws = window_seconds or self._default_window
        cutoff = time.time() - ws
        with self._lock:
            events = [e for e in self._events.get(tenant_id, []) if e.ts >= cutoff]
        return self._aggregate(tenant_id, events, ws)

    async def get_trend(
        self,
        tenant_id: str,
        windows: int = 12,
    ) -> list[QualityMetric]:
        ws = self._default_window
        now = time.time()
        result: list[QualityMetric] = []
        with self._lock:
            all_events = list(self._events.get(tenant_id, []))
        for i in range(windows - 1, -1, -1):
            end = now - i * ws
            start = end - ws
            bucket = [e for e in all_events if start <= e.ts < end]
            m = self._aggregate(tenant_id, bucket, ws)
            m.timestamp = end
            result.append(m)
        return result

    # ── helpers ────────────────────────────────────────────

    @staticmethod
    def _aggregate(
        tenant_id: str,
        events: list[_RawEvent],
        window_seconds: int,
    ) -> QualityMetric:
        thumbs_up = sum(1 for e in events if e.feedback_type == "thumbs_up")
        thumbs_down = sum(1 for e in events if e.feedback_type == "thumbs_down")
        bad_case_count = sum(
            1 for e in events if e.feedback_type in ("thumbs_down", "bad_case", "rating_1", "rating_2")
        )
        ratings = [e.rating for e in events if e.rating is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        total = len(events)
        satisfaction_rate = thumbs_up / total if total > 0 else 1.0  # 无数据默认满意

        return QualityMetric(
            tenant_id=tenant_id,
            window_seconds=window_seconds,
            total_requests=total,
            thumbs_up=thumbs_up,
            thumbs_down=thumbs_down,
            avg_rating=avg_rating,
            bad_case_count=bad_case_count,
            satisfaction_rate=satisfaction_rate,
            timestamp=time.time(),
        )


# ─────────────────────────── Singleton ───────────────────────

_aggregator: QualityAggregator | None = None
_agg_lock = threading.RLock()


def init_quality_monitor(window_seconds: int = 300) -> QualityAggregator:
    global _aggregator
    with _agg_lock:
        _aggregator = QualityAggregator(window_seconds=window_seconds)
        return _aggregator


def get_quality_monitor() -> QualityAggregator | None:
    return _aggregator


def reset_for_tests() -> None:
    global _aggregator
    with _agg_lock:
        _aggregator = None
