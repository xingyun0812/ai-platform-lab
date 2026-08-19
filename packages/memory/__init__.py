"""長記憶持久化 — Phase F #31

數據模型：
    MemoryRecord
        memory_id: str          # UUID
        tenant_id: str
        scope: str              # "user" | "tenant" | "session"
        scope_id: str           # user_id / tenant_id / session_id（user 範圍時 = user_id）
        content: str            # 記憶內容（文字）
        summary: str | None     # 可選摘要
        embedding: list[float] | None  # 可選向量（用於語義檢索）
        metadata: dict          # 任意附加元資料（來源、trace_id 等）
        created_at: float
        expires_at: float | None

儲存：
    PostgresMemoryStore（DATABASE_URL 可達時）— 持久化主存
    InMemoryMemoryStore（兜底）— 行程內
    RedisHotCache（REDIS_URL 可達時）— 熱資料快取（可選，疊加在 Postgres 之上）

API：
    add(memory) → memory_id
    get(memory_id) → MemoryRecord | None
    search(tenant_id, scope, scope_id, query, top_k, ...) → list[MemoryRecord]
    delete(memory_id)
    list_by_scope(tenant_id, scope, scope_id, limit) → list[MemoryRecord]

設計要點：
- scope 三級隔離：session 短期 / user 中期 / tenant 長期
- 語義檢索（embedding）可選；無 embedding 時降級為 keyword 模糊匹配
- TTL 通過 expires_at 控制；定時清理任務可選
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.memory.archive import (
    ArchivedRecord,
    ArchiveStore,
    InMemoryArchiveStore,
    PostgresArchiveStore,
    get_archive_store,
    init_archive_store,
    reset_archive_store_for_tests,
)
from packages.memory.config import MemoryGovernanceConfig
from packages.memory.governance.verify import Verdict, VerifyResult, verify_relevance
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
    quality_filter,
)

__all__ = [
    "ArchiveStore",
    "ArchivedRecord",
    "get_archive_store",
    "get_memory_metrics",
    "get_memory_store",
    "init_archive_store",
    "init_memory_store",
    "InMemoryArchiveStore",
    "InMemoryMemoryStore",
    "MemoryGovernanceConfig",
    "MemoryMetrics",
    "MemoryRecord",
    "MemoryStore",
    "PostgresArchiveStore",
    "PostgresMemoryStore",
    "quality_filter",
    "reset_archive_store_for_tests",
    "Verdict",
    "VerifyResult",
    "verify_relevance",
]
