from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis

logger = logging.getLogger("ai_platform.state.redis")

_client_lock = threading.Lock()
_client: redis.Redis | None = None
_client_url: str | None = None
_reachable_url: str | None | bool = False  # False=未探测, None=不可达, str=可用 URL


def redis_enabled(redis_url: str | None) -> bool:
    return get_effective_redis_url(redis_url) is not None


def get_effective_redis_url(redis_url: str | None) -> str | None:
    """探测 Redis 可达性；不可达时返回 None（调用方回退内存实现）。"""
    global _reachable_url
    url = (redis_url or "").strip()
    if not url:
        return None
    if _reachable_url is False:
        try:
            get_redis_client(url).ping()
            _reachable_url = url
            logger.info("redis connected url=%s", url)
        except Exception as e:
            _reachable_url = None
            logger.warning("REDIS_URL 不可达，回退进程内实现: %s", e)
    return _reachable_url if isinstance(_reachable_url, str) else None


def reset_redis_availability_for_tests() -> None:
    global _reachable_url
    _reachable_url = False


@lru_cache
def _import_redis():
    import redis as redis_lib

    return redis_lib


def get_redis_client(redis_url: str) -> redis.Redis:
    global _client, _client_url
    url = redis_url.strip()
    with _client_lock:
        if _client is not None and _client_url == url:
            return _client
        redis_lib = _import_redis()
        _client = redis_lib.Redis.from_url(url, decode_responses=True)
        _client_url = url
        return _client


def reset_redis_client_for_tests() -> None:
    global _client, _client_url, _reachable_url
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None
        _client_url = None
        _reachable_url = False
    get_redis_client.cache_clear()
