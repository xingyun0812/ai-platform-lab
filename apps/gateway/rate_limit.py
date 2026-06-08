from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitPolicy:
    rps: float
    burst: int


class TokenBucketLimiter:
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


_limiter_singleton: TokenBucketLimiter | None = None


def get_rate_limiter() -> TokenBucketLimiter:
    global _limiter_singleton
    if _limiter_singleton is None:
        _limiter_singleton = TokenBucketLimiter()
    return _limiter_singleton
