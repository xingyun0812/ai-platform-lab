"""HITL 审批队列存储 — Phase H #40

提供 InMemoryApprovalStore（默认）和 SqliteApprovalStore（可选）两种实现。
全局单例：init_approval_store / get_approval_store / reset_approval_store_for_tests
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 状态常量（StrEnum-兼容，同时向后兼容 packages/agent/hitl.py）
# ---------------------------------------------------------------------------

class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    # 兼容旧接口 confirmed → approved
    confirmed = "approved"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ApprovalRequest:
    request_id: str
    tenant_id: str
    session_id: str
    tool_name: str
    arguments: dict
    created_at: float
    expires_at: float
    status: str = "pending"
    decided_by: Optional[str] = None
    decided_at: Optional[float] = None
    decision_reason: Optional[str] = None
    webhook_sent: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "decision_reason": self.decision_reason,
            "webhook_sent": self.webhook_sent,
            "metadata": self.metadata,
        }


@dataclass
class ApprovalDecision:
    request_id: str
    status: str  # "approved" | "rejected" | "cancelled"
    decided_by: str
    reason: Optional[str]
    decided_at: float


@dataclass
class WebhookConfig:
    url: str
    headers: dict = field(default_factory=dict)
    secret: str = ""
    enabled: bool = True


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------

class ApprovalStore:
    """审批队列存储接口（抽象基类）。"""

    async def create(self, req: ApprovalRequest) -> str:
        raise NotImplementedError

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        raise NotImplementedError

    async def list_pending(self, tenant_id: str) -> list:
        raise NotImplementedError

    async def decide(self, decision: ApprovalDecision) -> Optional[ApprovalRequest]:
        raise NotImplementedError

    async def expire_stale(self) -> int:
        raise NotImplementedError

    async def cancel(self, request_id: str, by: str) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryApprovalStore
# ---------------------------------------------------------------------------

class InMemoryApprovalStore(ApprovalStore):
    """基于 dict 的线程安全内存存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, ApprovalRequest] = {}

    async def create(self, req: ApprovalRequest) -> str:
        with self._lock:
            self._store[req.request_id] = req
        return req.request_id

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._store.get(request_id)

    async def list_pending(self, tenant_id: str) -> list:
        with self._lock:
            return [
                r for r in self._store.values()
                if r.status == "pending" and r.tenant_id == tenant_id
            ]

    async def decide(self, decision: ApprovalDecision) -> Optional[ApprovalRequest]:
        with self._lock:
            req = self._store.get(decision.request_id)
            if req is None:
                return None
            if req.status != "pending":
                return None
            req.status = decision.status
            req.decided_by = decision.decided_by
            req.decided_at = decision.decided_at
            req.decision_reason = decision.reason
            return req

    async def expire_stale(self) -> int:
        now = time.time()
        count = 0
        with self._lock:
            for req in self._store.values():
                if req.status == "pending" and req.expires_at < now:
                    req.status = "timeout"
                    count += 1
        return count

    async def cancel(self, request_id: str, by: str) -> bool:
        with self._lock:
            req = self._store.get(request_id)
            if req is None:
                return False
            if req.status != "pending":
                return False
            req.status = "cancelled"
            req.decided_by = by
            req.decided_at = time.time()
            return True


# ---------------------------------------------------------------------------
# SqliteApprovalStore
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS hitl_approvals (
    request_id      TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    arguments       TEXT NOT NULL,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    decided_by      TEXT,
    decided_at      REAL,
    decision_reason TEXT,
    webhook_sent    INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}'
)
"""


class SqliteApprovalStore(ApprovalStore):
    """SQLite 持久化存储。database_url 格式：sqlite:///path/to/db 或 sqlite:///:memory:"""

    def __init__(self, database_url: str) -> None:
        import json as _json
        self._json = _json
        path = database_url[len("sqlite:///"):]  # strip prefix
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute(_CREATE_TABLE)
            self._conn.commit()

    def _row_to_req(self, row) -> ApprovalRequest:
        import json as _json
        return ApprovalRequest(
            request_id=row[0],
            tenant_id=row[1],
            session_id=row[2],
            tool_name=row[3],
            arguments=_json.loads(row[4]),
            created_at=row[5],
            expires_at=row[6],
            status=row[7],
            decided_by=row[8],
            decided_at=row[9],
            decision_reason=row[10],
            webhook_sent=bool(row[11]),
            metadata=_json.loads(row[12]),
        )

    async def create(self, req: ApprovalRequest) -> str:
        import json as _json
        with self._lock:
            self._conn.execute(
                """INSERT INTO hitl_approvals
                   (request_id,tenant_id,session_id,tool_name,arguments,
                    created_at,expires_at,status,decided_by,decided_at,
                    decision_reason,webhook_sent,metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    req.request_id, req.tenant_id, req.session_id,
                    req.tool_name, _json.dumps(req.arguments),
                    req.created_at, req.expires_at, req.status,
                    req.decided_by, req.decided_at, req.decision_reason,
                    int(req.webhook_sent), _json.dumps(req.metadata),
                ),
            )
            self._conn.commit()
        return req.request_id

    async def get(self, request_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM hitl_approvals WHERE request_id=?", (request_id,)
            )
            row = cur.fetchone()
        return self._row_to_req(row) if row else None

    async def list_pending(self, tenant_id: str) -> list:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM hitl_approvals WHERE status='pending' AND tenant_id=?",
                (tenant_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_req(r) for r in rows]

    async def decide(self, decision: ApprovalDecision) -> Optional[ApprovalRequest]:
        import json as _json  # noqa: F401
        with self._lock:
            cur = self._conn.execute(
                "SELECT status FROM hitl_approvals WHERE request_id=?",
                (decision.request_id,),
            )
            row = cur.fetchone()
            if row is None or row[0] != "pending":
                return None
            self._conn.execute(
                """UPDATE hitl_approvals
                   SET status=?,decided_by=?,decided_at=?,decision_reason=?
                   WHERE request_id=?""",
                (
                    decision.status, decision.decided_by,
                    decision.decided_at, decision.reason,
                    decision.request_id,
                ),
            )
            self._conn.commit()
        return await self.get(decision.request_id)

    async def expire_stale(self) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE hitl_approvals SET status='timeout' WHERE status='pending' AND expires_at<?",
                (now,),
            )
            self._conn.commit()
        return cur.rowcount  # type: ignore[return-value]

    async def cancel(self, request_id: str, by: str) -> bool:
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """UPDATE hitl_approvals
                   SET status='cancelled',decided_by=?,decided_at=?
                   WHERE request_id=? AND status='pending'""",
                (by, now, request_id),
            )
            self._conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_store: Optional[ApprovalStore] = None
_store_lock = threading.Lock()


def init_approval_store(database_url: Optional[str] = None) -> ApprovalStore:
    """初始化并返回全局 ApprovalStore 单例。"""
    global _store
    with _store_lock:
        if database_url and database_url.startswith("sqlite:"):
            _store = SqliteApprovalStore(database_url)
        else:
            _store = InMemoryApprovalStore()
    return _store


def get_approval_store() -> Optional[ApprovalStore]:
    """获取全局 ApprovalStore；未初始化时返回 None。"""
    return _store


def reset_approval_store_for_tests() -> None:
    """测试专用：重置全局单例。"""
    global _store
    with _store_lock:
        _store = None
