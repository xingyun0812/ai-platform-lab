from __future__ import annotations

import datetime as dt
import threading
from abc import ABC, abstractmethod
from collections import defaultdict

from apps.gateway.settings import get_settings

_quota_singleton: DailyQuotaTracker | None = None

_QUOTA_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local count = tonumber(redis.call('GET', key) or '0')
if count >= limit then
  return 0
end
local new_count = redis.call('INCR', key)
if new_count == 1 then
  redis.call('EXPIRE', key, ttl)
end
return 1
"""


class DailyQuotaTracker(ABC):
    @abstractmethod
    def current(self, tenant_id: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def has_quota(self, tenant_id: str, limit: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def try_consume(self, tenant_id: str, limit: int) -> bool:
        raise NotImplementedError


class InMemoryDailyQuotaTracker(DailyQuotaTracker):
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

    def has_quota(self, tenant_id: str, limit: int) -> bool:
        if limit < 0:
            return True
        day = self._today_key()
        with self._lock:
            return self._counts[(tenant_id, day)] < limit

    def try_consume(self, tenant_id: str, limit: int) -> bool:
        if limit < 0:
            return True
        day = self._today_key()
        with self._lock:
            key = (tenant_id, day)
            if self._counts[key] >= limit:
                return False
            self._counts[key] += 1
            return True


class RedisDailyQuotaTracker(DailyQuotaTracker):
    """Redis 共享日配额；多 gateway 实例一致，UTC 日切。"""

    def __init__(self, redis_url: str, *, key_prefix: str = "ai_platform:quota") -> None:
        from packages.state.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._prefix = key_prefix
        self._quota_script = self._redis.register_script(_QUOTA_LUA)

    def _day(self) -> str:
        return dt.datetime.now(dt.UTC).date().isoformat()

    def _key(self, tenant_id: str) -> str:
        return f"{self._prefix}:{tenant_id}:{self._day()}"

    def current(self, tenant_id: str) -> int:
        raw = self._redis.get(self._key(tenant_id))
        return int(raw) if raw else 0

    def has_quota(self, tenant_id: str, limit: int) -> bool:
        if limit < 0:
            return True
        return self.current(tenant_id) < limit

    def try_consume(self, tenant_id: str, limit: int) -> bool:
        if limit < 0:
            return True
        allowed = self._quota_script(keys=[self._key(tenant_id)], args=[limit, 172800])
        return bool(int(allowed))


def get_quota_tracker() -> DailyQuotaTracker:
    global _quota_singleton
    if _quota_singleton is not None:
        return _quota_singleton
    from packages.state.redis_client import get_effective_redis_url

    settings = get_settings()
    redis_url = get_effective_redis_url(settings.redis_url)
    if redis_url:
        _quota_singleton = RedisDailyQuotaTracker(redis_url)
    else:
        _quota_singleton = InMemoryDailyQuotaTracker()
    return _quota_singleton


def reset_quota_tracker_for_tests() -> None:
    global _quota_singleton
    _quota_singleton = None
