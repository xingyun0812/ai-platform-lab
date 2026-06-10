from __future__ import annotations

import json
import logging
from typing import Any

from packages.agent.session_state import SessionState, parse_session_raw, serialize_session

logger = logging.getLogger("ai_platform.agent.session_redis")


class RedisSessionStore:
    def __init__(self, redis_url: str, *, ttl_seconds: int = 86400) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds
        self._prefix = "ai_platform:session:"

    def _key(self, tenant_id: str, session_id: str) -> str:
        return f"{self._prefix}{tenant_id}:{session_id}"

    def get_session_state(self, tenant_id: str, session_id: str) -> SessionState:
        raw = self._client.get(self._key(tenant_id, session_id))
        if not raw:
            return SessionState(messages=[])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return SessionState(messages=[])
        return parse_session_raw(data)

    def save_session_state(self, tenant_id: str, session_id: str, state: SessionState) -> None:
        key = self._key(tenant_id, session_id)
        self._client.setex(key, self._ttl, serialize_session(state))

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
