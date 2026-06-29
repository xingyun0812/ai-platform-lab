"""Gateway 薄 re-export — 实现位于 packages.rag.task_store（Issue #152）。"""

from packages.rag.task_store import (
    IndexTaskRecord,
    IndexTaskStore,
    InMemoryIndexTaskStore,
    RedisIndexTaskStore,
    get_task_store,
    reset_task_store_for_tests,
)

__all__ = [
    "IndexTaskRecord",
    "IndexTaskStore",
    "InMemoryIndexTaskStore",
    "RedisIndexTaskStore",
    "get_task_store",
    "reset_task_store_for_tests",
]
