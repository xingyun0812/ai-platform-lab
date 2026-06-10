from __future__ import annotations

import threading
from typing import Any

from packages.agent.session_state import SessionState


class SessionStore:
    """进程内 (tenant_id, session_id) → SessionState。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], SessionState] = {}

    def get_session_state(self, tenant_id: str, session_id: str) -> SessionState:
        key = (tenant_id, session_id)
        with self._lock:
            state = self._sessions.get(key)
            if state is None:
                return SessionState(messages=[])
            return SessionState(
                messages=list(state.messages),
                summary=state.summary,
                turn_count=state.turn_count,
            )

    def save_session_state(self, tenant_id: str, session_id: str, state: SessionState) -> None:
        key = (tenant_id, session_id)
        with self._lock:
            self._sessions[key] = SessionState(
                messages=list(state.messages),
                summary=state.summary,
                turn_count=state.turn_count,
            )

    def get_messages(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        return self.get_session_state(tenant_id, session_id).messages

    def save_messages(self, tenant_id: str, session_id: str, messages: list[dict[str, Any]]) -> None:
        prev = self.get_session_state(tenant_id, session_id)
        self.save_session_state(
            tenant_id,
            session_id,
            SessionState(
                messages=list(messages),
                summary=prev.summary,
                turn_count=prev.turn_count,
            ),
        )


_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is not None:
        return _session_store
    from apps.gateway.settings import get_settings

    redis_url = (get_settings().redis_url or "").strip()
    if redis_url:
        try:
            from packages.agent.session_redis import RedisSessionStore

            _session_store = RedisSessionStore(redis_url)  # type: ignore[assignment]
            return _session_store
        except Exception:
            pass
    _session_store = SessionStore()
    return _session_store
