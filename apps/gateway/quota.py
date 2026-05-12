from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict


class DailyQuotaTracker:
    """进程内按「租户 + UTC 日期」计数；重启清零。适合本地实验。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], int] = defaultdict(int)

    def _today_key(self) -> str:
        return dt.datetime.now(dt.UTC).date().isoformat()

    def current(self, tenant_id: str) -> int:
        day = self._today_key()
        with self._lock:
            return self._counts[(tenant_id, day)]

    def try_consume(self, tenant_id: str, limit: int) -> bool:
        """limit == -1 表示不限。返回是否允许本次请求。"""
        if limit < 0:
            return True
        day = self._today_key()
        with self._lock:
            key = (tenant_id, day)
            if self._counts[key] >= limit:
                return False
            self._counts[key] += 1
            return True
