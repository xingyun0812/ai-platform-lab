from __future__ import annotations

from abc import ABC, abstractmethod

from packages.platform import get_settings


class IndexTaskQueue(ABC):
    @abstractmethod
    def enqueue(self, task_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def dequeue_blocking(self, timeout_seconds: int = 5) -> str | None:
        raise NotImplementedError


class RedisIndexTaskQueue(IndexTaskQueue):
    def __init__(self, redis_url: str, *, queue_name: str) -> None:
        from packages.state.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._queue_name = queue_name

    def enqueue(self, task_id: str) -> None:
        self._redis.rpush(self._queue_name, task_id)

    def dequeue_blocking(self, timeout_seconds: int = 5) -> str | None:
        result = self._redis.blpop(self._queue_name, timeout=max(1, timeout_seconds))
        if not result:
            return None
        return str(result[1])


_queue_singleton: IndexTaskQueue | None = None


def get_index_task_queue() -> IndexTaskQueue | None:
    """未配置 Redis 或未启用 worker 模式时返回 None。"""
    global _queue_singleton
    from packages.state.redis_client import get_effective_redis_url

    settings = get_settings()
    redis_url = get_effective_redis_url(settings.redis_url)
    if not redis_url or not settings.use_index_worker:
        return None
    if _queue_singleton is None:
        _queue_singleton = RedisIndexTaskQueue(
            redis_url,
            queue_name=settings.index_queue_name,
        )
    return _queue_singleton


def reset_index_task_queue_for_tests() -> None:
    global _queue_singleton
    _queue_singleton = None
