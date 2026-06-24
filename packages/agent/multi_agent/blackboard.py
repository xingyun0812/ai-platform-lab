"""Multi-Agent 共享黑板 — Phase O #89

Redis key: ``ai_platform:blackboard:{tenant_id}:{session_id}``
不可达 Redis 时回退进程内存储（与 session / quota 模式一致）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

logger = logging.getLogger("ai_platform.multi_agent.blackboard")

_PREFIX = "ai_platform:blackboard:"
_DEFAULT_TTL = 86400


@dataclass
class BlackboardEntry:
    entry_id: str
    agent_id: str
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    kind: str = "delegation"  # delegation | review | note

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlackboardEntry:
        return cls(
            entry_id=str(data.get("entry_id") or uuid.uuid4().hex[:12]),
            agent_id=str(data.get("agent_id") or ""),
            role=str(data.get("role") or "specialist"),
            content=str(data.get("content") or ""),
            ts=float(data.get("ts") or time.time()),
            kind=str(data.get("kind") or "delegation"),
        )


class BlackboardStore(Protocol):
    def append(
        self,
        tenant_id: str,
        session_id: str,
        *,
        agent_id: str,
        role: str,
        content: str,
        kind: str = "delegation",
    ) -> BlackboardEntry: ...

    def list_entries(
        self,
        tenant_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[BlackboardEntry]: ...

    def clear(self, tenant_id: str, session_id: str) -> None: ...


def format_entries_for_reviewer(entries: list[BlackboardEntry]) -> str:
    if not entries:
        return ""
    lines: list[str] = []
    for e in entries:
        lines.append(f"[{e.role}:{e.agent_id}] {e.content}")
    return "\n".join(lines)


class InMemoryBlackboardStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[str, str], list[BlackboardEntry]] = {}

    def append(
        self,
        tenant_id: str,
        session_id: str,
        *,
        agent_id: str,
        role: str,
        content: str,
        kind: str = "delegation",
    ) -> BlackboardEntry:
        entry = BlackboardEntry(
            entry_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            role=role,
            content=content,
            kind=kind,
        )
        key = (tenant_id, session_id)
        with self._lock:
            self._data.setdefault(key, []).append(entry)
        return entry

    def list_entries(
        self,
        tenant_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        key = (tenant_id, session_id)
        with self._lock:
            items = list(self._data.get(key, []))
        return items[-limit:]

    def clear(self, tenant_id: str, session_id: str) -> None:
        key = (tenant_id, session_id)
        with self._lock:
            self._data.pop(key, None)


class RedisBlackboardStore:
    def __init__(self, redis_url: str, *, ttl_seconds: int = _DEFAULT_TTL) -> None:
        from packages.state.redis_client import get_redis_client

        self._client = get_redis_client(redis_url)
        self._ttl = ttl_seconds

    def _key(self, tenant_id: str, session_id: str) -> str:
        return f"{_PREFIX}{tenant_id}:{session_id}"

    def append(
        self,
        tenant_id: str,
        session_id: str,
        *,
        agent_id: str,
        role: str,
        content: str,
        kind: str = "delegation",
    ) -> BlackboardEntry:
        entry = BlackboardEntry(
            entry_id=uuid.uuid4().hex[:12],
            agent_id=agent_id,
            role=role,
            content=content,
            kind=kind,
        )
        key = self._key(tenant_id, session_id)
        raw = self._client.get(key)
        items: list[dict[str, Any]] = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    items = parsed
            except json.JSONDecodeError:
                items = []
        items.append(entry.to_dict())
        self._client.setex(key, self._ttl, json.dumps(items, ensure_ascii=False))
        return entry

    def list_entries(
        self,
        tenant_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        raw = self._client.get(self._key(tenant_id, session_id))
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        entries = [BlackboardEntry.from_dict(x) for x in parsed if isinstance(x, dict)]
        return entries[-limit:]

    def clear(self, tenant_id: str, session_id: str) -> None:
        self._client.delete(self._key(tenant_id, session_id))


_blackboard: BlackboardStore | None = None


def get_blackboard() -> BlackboardStore:
    global _blackboard
    if _blackboard is not None:
        return _blackboard
    from apps.gateway.settings import get_settings
    from packages.state.redis_client import get_effective_redis_url

    settings = get_settings()
    url = get_effective_redis_url(settings.redis_url)
    if url:
        try:
            _blackboard = RedisBlackboardStore(url)
            return _blackboard
        except Exception as e:
            logger.warning("blackboard redis init failed, fallback memory: %s", e)
    _blackboard = InMemoryBlackboardStore()
    return _blackboard


def reset_blackboard_for_tests() -> None:
    global _blackboard
    _blackboard = InMemoryBlackboardStore()
