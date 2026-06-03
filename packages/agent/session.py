from __future__ import annotations

import threading
from typing import Any


class SessionStore:
    """进程内 (tenant_id, session_id) → messages 列表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def get_messages(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        key = (tenant_id, session_id)
        with self._lock:
            return list(self._sessions.get(key, []))

    def save_messages(self, tenant_id: str, session_id: str, messages: list[dict[str, Any]]) -> None:
        key = (tenant_id, session_id)
        with self._lock:
            self._sessions[key] = list(messages)


_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store
