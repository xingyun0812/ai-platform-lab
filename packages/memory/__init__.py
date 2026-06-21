"""长记忆持久化 — Phase F #31

数据模型：
    MemoryRecord
        memory_id: str          # UUID
        tenant_id: str
        scope: str              # "user" | "tenant" | "session"
        scope_id: str           # user_id / tenant_id / session_id（user 范围时 = user_id）
        content: str            # 记忆内容（文本）
        summary: str | None     # 可选摘要
        embedding: list[float] | None  # 可选向量（用于语义检索）
        metadata: dict          # 任意附加元数据（来源、trace_id 等）
        created_at: float
        expires_at: float | None

存储：
    PostgresMemoryStore（DATABASE_URL 可达时）— 持久化主存
    InMemoryMemoryStore（兜底）— 进程内
    RedisHotCache（REDIS_URL 可达时）— 热数据缓存（可选，叠加在 Postgres 之上）

API：
    add(memory) → memory_id
    get(memory_id) → MemoryRecord | None
    search(tenant_id, scope, scope_id, query, top_k, ...) → list[MemoryRecord]
    delete(memory_id)
    list_by_scope(tenant_id, scope, scope_id, limit) → list[MemoryRecord]

设计要点：
- scope 三级隔离：session 短期 / user 中期 / tenant 长期
- 语义检索（embedding）可选；无 embedding 时降级为 keyword 模糊匹配
- TTL 通过 expires_at 控制；定时清理任务可选
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.memory.metrics import (
    MemoryMetrics,
    get_memory_metrics,
)
from packages.memory.store import (
    InMemoryMemoryStore,
    MemoryRecord,
    MemoryStore,
    PostgresMemoryStore,
    get_memory_store,
    init_memory_store,
)

__all__ = [
    "InMemoryMemoryStore",
    "MemoryMetrics",
    "MemoryRecord",
    "MemoryStore",
    "PostgresMemoryStore",
    "get_memory_metrics",
    "get_memory_store",
    "init_memory_store",
]
