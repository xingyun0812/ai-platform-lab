"""动作审计日志 — Phase I #42

ActionAuditEntry + ActionAuditLogger，支持内存存储（可扩展至 SQLite）。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ActionAuditEntry:
    """单条工具动作审计记录。"""

    entry_id: str
    tenant_id: str
    session_id: str
    tool_name: str
    action_level: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    status: str = "success"          # success | failed | denied | pending
    created_at: float = field(default_factory=time.time)
    decided_by: str | None = None
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "action_level": self.action_level,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "status": self.status,
            "created_at": self.created_at,
            "decided_by": self.decided_by,
            "approval_id": self.approval_id,
        }


# ---------------------------------------------------------------------------
# 内存存储实现
# ---------------------------------------------------------------------------

class _InMemoryActionStore:
    """线程安全的内存存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, ActionAuditEntry] = {}

    async def add(self, entry: ActionAuditEntry) -> str:
        with self._lock:
            self._entries[entry.entry_id] = entry
        return entry.entry_id

    async def get(self, entry_id: str) -> ActionAuditEntry | None:
        with self._lock:
            return self._entries.get(entry_id)

    async def list_by_tenant(
        self,
        tenant_id: str,
        action_level: str | None = None,
        limit: int = 50,
    ) -> list[ActionAuditEntry]:
        with self._lock:
            items = [
                e for e in self._entries.values() if e.tenant_id == tenant_id
            ]
        if action_level is not None:
            items = [e for e in items if e.action_level == action_level]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]


# ---------------------------------------------------------------------------
# ActionAuditLogger
# ---------------------------------------------------------------------------

class ActionAuditLogger:
    """工具动作审计记录器。"""

    def __init__(self, database_url: str | None = None) -> None:
        # 目前仅实现内存存储；database_url 预留给将来 SQLite/Postgres
        self._store = _InMemoryActionStore()
        self._database_url = database_url

    async def log_action(self, entry: ActionAuditEntry) -> str:
        """记录动作审计条目，返回 entry_id。"""
        return await self._store.add(entry)

    async def list_actions(
        self,
        tenant_id: str,
        action_level: str | None = None,
        limit: int = 50,
    ) -> list[ActionAuditEntry]:
        """按租户（可选按 action_level）列出审计记录。"""
        return await self._store.list_by_tenant(
            tenant_id, action_level=action_level, limit=limit
        )

    async def list_destructive_actions(
        self, tenant_id: str, limit: int = 50
    ) -> list[ActionAuditEntry]:
        """列出 destructive 级别的动作审计记录。"""
        from packages.audit.action_levels import ActionLevel

        return await self._store.list_by_tenant(
            tenant_id, action_level=ActionLevel.DESTRUCTIVE, limit=limit
        )

    async def get_action(self, entry_id: str) -> ActionAuditEntry | None:
        """按 entry_id 获取单条记录。"""
        return await self._store.get(entry_id)


def _make_entry(
    *,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    action_level: str,
    arguments: dict | None = None,
    result_summary: str = "",
    status: str = "success",
    decided_by: str | None = None,
    approval_id: str | None = None,
) -> ActionAuditEntry:
    """工厂函数：生成带 UUID entry_id 的审计条目。"""
    return ActionAuditEntry(
        entry_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        session_id=session_id,
        tool_name=tool_name,
        action_level=action_level,
        arguments=arguments or {},
        result_summary=result_summary,
        status=status,
        created_at=time.time(),
        decided_by=decided_by,
        approval_id=approval_id,
    )


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_logger_singleton: ActionAuditLogger | None = None
_singleton_lock = threading.Lock()


def init_action_logger(database_url: str | None = None) -> ActionAuditLogger:
    global _logger_singleton
    with _singleton_lock:
        if _logger_singleton is None:
            _logger_singleton = ActionAuditLogger(database_url=database_url)
    return _logger_singleton


def get_action_logger() -> ActionAuditLogger | None:
    return _logger_singleton


def reset_for_tests() -> None:
    global _logger_singleton
    with _singleton_lock:
        _logger_singleton = None
