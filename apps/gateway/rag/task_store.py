from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from packages.contracts.rag_schemas import TaskStatus


@dataclass
class IndexTaskRecord:
    task_id: str
    kb_id: str
    version: int
    source_uri: str
    status: TaskStatus = TaskStatus.pending
    error: str | None = None
    chunks_indexed: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class IndexTaskStore:
    """进程内任务表；重启后任务记录丢失（第 2 周实验可接受）。"""

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
            record.updated_at = datetime.now(UTC)
            return record
