from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from apps.gateway.settings import get_settings

_BUCKET_LUA = """
local key = KEYS[1]
local rps = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
  tokens = burst
  last = now
end
local elapsed = math.max(0, now - last)
tokens = math.min(burst, tokens + elapsed * rps)
if tokens < 1 then
  local retry = (1 - tokens) / rps
  redis.call('HMSET', key, 'tokens', tokens, 'last', now)
  redis.call('EXPIRE', key, 3600)
  return {0, tostring(retry)}
end
tokens = tokens - 1
redis.call('HMSET', key, 'tokens', tokens, 'last', now)
redis.call('EXPIRE', key, 3600)
return {1, '0'}
"""


@dataclass(frozen=True)
class RateLimitPolicy:
    rps: float
    burst: int


class RateLimiter(ABC):
    @abstractmethod
    def try_acquire(self, tenant_id: str, policy: RateLimitPolicy) -> bool:
        raise NotImplementedError

    @abstractmethod
    def retry_after_seconds(self, tenant_id: str, policy: RateLimitPolicy) -> float:
        raise NotImplementedError


class InMemoryTokenBucketLimiter(RateLimiter):
    """进程内租户级令牌桶；适合本地演示，重启后状态清零。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}

    def _refill(self, tenant_id: str, policy: RateLimitPolicy, now: float) -> float:
        last = self._last_refill.get(tenant_id, now)
        elapsed = max(0.0, now - last)
        current = self._tokens.get(tenant_id, float(policy.burst))
        current = min(float(policy.burst), current + elapsed * policy.rps)
        self._tokens[tenant_id] = current
        self._last_refill[tenant_id] = now
        return current

    def try_acquire(self, tenant_id: str, policy: RateLimitPolicy) -> bool:
        if policy.rps <= 0 or policy.burst <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            tokens = self._refill(tenant_id, policy, now)
            if tokens < 1.0:
                return False
            self._tokens[tenant_id] = tokens - 1.0
            return True

    def retry_after_seconds(self, tenant_id: str, policy: RateLimitPolicy) -> float:
        now = time.monotonic()
        with self._lock:
            tokens = self._refill(tenant_id, policy, now)
            if tokens >= 1.0:
                return 0.0
            needed = 1.0 - tokens
            return max(0.05, needed / policy.rps)


class RedisTokenBucketLimiter(RateLimiter):
    """Redis 共享令牌桶；多 gateway 实例限速一致。"""

    def __init__(self, redis_url: str, *, key_prefix: str = "ai_platform:ratelimit") -> None:
        from packages.state.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._prefix = key_prefix
        self._bucket_script = self._redis.register_script(_BUCKET_LUA)

    def _key(self, tenant_id: str) -> str:
        return f"{self._prefix}:{tenant_id}"

    def _eval(self, tenant_id: str, policy: RateLimitPolicy) -> tuple[bool, float]:
        if policy.rps <= 0 or policy.burst <= 0:
            return True, 0.0
        now = time.monotonic()
        result = self._bucket_script(
            keys=[self._key(tenant_id)],
            args=[policy.rps, policy.burst, now],
        )
        allowed = bool(int(result[0]))
        retry = float(result[1])
        return allowed, retry

    def try_acquire(self, tenant_id: str, policy: RateLimitPolicy) -> bool:
        allowed, _ = self._eval(tenant_id, policy)
        return allowed

    def retry_after_seconds(self, tenant_id: str, policy: RateLimitPolicy) -> float:
        _, retry = self._eval(tenant_id, policy)
        return max(0.05, retry)


_limiter_singleton: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter_singleton
    if _limiter_singleton is not None:
        return _limiter_singleton
    from packages.state.redis_client import get_effective_redis_url

    settings = get_settings()
    redis_url = get_effective_redis_url(settings.redis_url)
    if redis_url:
        _limiter_singleton = RedisTokenBucketLimiter(redis_url)
    else:
        _limiter_singleton = InMemoryTokenBucketLimiter()
    return _limiter_singleton


def reset_rate_limiter_for_tests() -> None:
    global _limiter_singleton
    _limiter_singleton = None
