"""RAG 索引任务元数据 store — 内存 / Redis（Issue #152 PR-1）。"""

from __future__ import annotations

import json
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from packages.contracts.rag_schemas import TaskStatus
from packages.platform import get_settings


@dataclass
class IndexTaskRecord:
    task_id: str
    kb_id: str
    version: int
    source_uri: str
    status: TaskStatus = TaskStatus.pending
    error: str | None = None
    chunks_indexed: int | None = None
    new_chunks: int | None = None
    updated_chunks: int | None = None
    skipped_chunks: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class IndexTaskStore(ABC):
    @abstractmethod
    def create(self, *, kb_id: str, version: int, source_uri: str) -> IndexTaskRecord:
        raise NotImplementedError

    @abstractmethod
    def get(self, task_id: str) -> IndexTaskRecord | None:
        raise NotImplementedError

    @abstractmethod
    def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        error: str | None = None,
        chunks_indexed: int | None = None,
        new_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
    ) -> IndexTaskRecord | None:
        raise NotImplementedError


class InMemoryIndexTaskStore(IndexTaskStore):
    """进程内任务表；重启后任务记录丢失（本地零 Redis 可接受）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, IndexTaskRecord] = {}

    def create(self, *, kb_id: str, version: int, source_uri: str) -> IndexTaskRecord:
        task_id = str(uuid.uuid4())
        record = IndexTaskRecord(
            task_id=task_id,
            kb_id=kb_id,
            version=version,
            source_uri=source_uri,
        )
        with self._lock:
            self._tasks[task_id] = record
        return record

    def get(self, task_id: str) -> IndexTaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        error: str | None = None,
        chunks_indexed: int | None = None,
        new_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
    ) -> IndexTaskRecord | None:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            if status is not None:
                record.status = status
            if error is not None:
                record.error = error
            if chunks_indexed is not None:
                record.chunks_indexed = chunks_indexed
            if new_chunks is not None:
                record.new_chunks = new_chunks
            if updated_chunks is not None:
                record.updated_chunks = updated_chunks
            if skipped_chunks is not None:
                record.skipped_chunks = skipped_chunks
            record.updated_at = datetime.now(UTC)
            return record


def _record_to_json(record: IndexTaskRecord) -> str:
    payload = asdict(record)
    payload["status"] = record.status.value
    payload["created_at"] = record.created_at.isoformat()
    payload["updated_at"] = record.updated_at.isoformat()
    return json.dumps(payload, ensure_ascii=False)


def _record_from_json(raw: str) -> IndexTaskRecord:
    data = json.loads(raw)
    return IndexTaskRecord(
        task_id=data["task_id"],
        kb_id=data["kb_id"],
        version=int(data["version"]),
        source_uri=data["source_uri"],
        status=TaskStatus(data["status"]),
        error=data.get("error"),
        chunks_indexed=data.get("chunks_indexed"),
        new_chunks=data.get("new_chunks"),
        updated_chunks=data.get("updated_chunks"),
        skipped_chunks=data.get("skipped_chunks"),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


class RedisIndexTaskStore(IndexTaskStore):
    """Redis 共享任务元数据；gateway 创建、worker 更新。"""

    def __init__(self, redis_url: str, *, key_prefix: str = "ai_platform:task", ttl_seconds: int = 604800) -> None:
        from packages.state.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}:{task_id}"

    def create(self, *, kb_id: str, version: int, source_uri: str) -> IndexTaskRecord:
        task_id = str(uuid.uuid4())
        record = IndexTaskRecord(
            task_id=task_id,
            kb_id=kb_id,
            version=version,
            source_uri=source_uri,
        )
        self._redis.set(self._key(task_id), _record_to_json(record), ex=self._ttl)
        return record

    def get(self, task_id: str) -> IndexTaskRecord | None:
        raw = self._redis.get(self._key(task_id))
        if not raw:
            return None
        return _record_from_json(raw)

    def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        error: str | None = None,
        chunks_indexed: int | None = None,
        new_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
    ) -> IndexTaskRecord | None:
        record = self.get(task_id)
        if not record:
            return None
        if status is not None:
            record.status = status
        if error is not None:
            record.error = error
        if chunks_indexed is not None:
            record.chunks_indexed = chunks_indexed
        if new_chunks is not None:
            record.new_chunks = new_chunks
        if updated_chunks is not None:
            record.updated_chunks = updated_chunks
        if skipped_chunks is not None:
            record.skipped_chunks = skipped_chunks
        record.updated_at = datetime.now(UTC)
        self._redis.set(self._key(task_id), _record_to_json(record), ex=self._ttl)
        return record


_store_singleton: IndexTaskStore | None = None


def get_task_store() -> IndexTaskStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    from packages.state.redis_client import get_effective_redis_url

    settings = get_settings()
    redis_url = get_effective_redis_url(settings.redis_url)
    if redis_url and settings.use_index_worker:
        _store_singleton = RedisIndexTaskStore(redis_url)
    else:
        _store_singleton = InMemoryIndexTaskStore()
    return _store_singleton


def reset_task_store_for_tests() -> None:
    global _store_singleton
    _store_singleton = None
