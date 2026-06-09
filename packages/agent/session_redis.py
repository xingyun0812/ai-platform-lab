from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ai_platform.agent.session_redis")


class RedisSessionStore:
    def __init__(self, redis_url: str, *, ttl_seconds: int = 86400) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds
        self._prefix = "ai_platform:session:"

    def _key(self, tenant_id: str, session_id: str) -> str:
        return f"{self._prefix}{tenant_id}:{session_id}"

    def get_messages(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        raw = self._client.get(self._key(tenant_id, session_id))
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def save_messages(self, tenant_id: str, session_id: str, messages: list[dict[str, Any]]) -> None:
        key = self._key(tenant_id, session_id)
        self._client.setex(key, self._ttl, json.dumps(messages, ensure_ascii=False))
