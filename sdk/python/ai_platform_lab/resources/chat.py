"""Chat completions resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class Completions(BaseResource):
    """Sync chat completions sub-resource."""

    def create(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        """POST /v1/chat/completions."""
        payload = {"model": model, "messages": messages, **kwargs}
        return self._request("POST", "/v1/chat/completions", json=payload)


class AsyncCompletions(AsyncBaseResource):
    """Async chat completions sub-resource."""

    async def create(self, model: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        """POST /v1/chat/completions."""
        payload = {"model": model, "messages": messages, **kwargs}
        return await self._request("POST", "/v1/chat/completions", json=payload)


class ChatResource(BaseResource):
    """Sync chat resource."""

    @property
    def completions(self) -> Completions:
        return Completions(self._client, self._base_url, self._api_key, self._tenant_id)


class AsyncChatResource(AsyncBaseResource):
    """Async chat resource."""

    @property
    def completions(self) -> AsyncCompletions:
        return AsyncCompletions(self._client, self._base_url, self._api_key, self._tenant_id)
