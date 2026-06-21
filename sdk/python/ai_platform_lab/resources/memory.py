"""Memory resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class MemoryResource(BaseResource):
    """Sync memory resource."""

    def list(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """GET /v1/memory — list memories, optionally filtered by tenant_id."""
        params: dict[str, str] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        return self._request("GET", "/v1/memory", params=params)

    def get(self, memory_id: str) -> dict[str, Any]:
        """GET /v1/memory/{memory_id}."""
        return self._request("GET", f"/v1/memory/{memory_id}")

    def create(self, tenant_id: str, content: str, metadata: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/memory — store a new memory entry."""
        payload: dict[str, Any] = {"tenant_id": tenant_id, "content": content, **kwargs}
        if metadata is not None:
            payload["metadata"] = metadata
        return self._request("POST", "/v1/memory", json=payload)

    def delete(self, memory_id: str) -> dict[str, Any]:
        """DELETE /v1/memory/{memory_id}."""
        return self._request("DELETE", f"/v1/memory/{memory_id}")


class AsyncMemoryResource(AsyncBaseResource):
    """Async memory resource."""

    async def list(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """GET /v1/memory."""
        params: dict[str, str] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        return await self._request("GET", "/v1/memory", params=params)

    async def get(self, memory_id: str) -> dict[str, Any]:
        """GET /v1/memory/{memory_id}."""
        return await self._request("GET", f"/v1/memory/{memory_id}")

    async def create(self, tenant_id: str, content: str, metadata: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/memory."""
        payload: dict[str, Any] = {"tenant_id": tenant_id, "content": content, **kwargs}
        if metadata is not None:
            payload["metadata"] = metadata
        return await self._request("POST", "/v1/memory", json=payload)

    async def delete(self, memory_id: str) -> dict[str, Any]:
        """DELETE /v1/memory/{memory_id}."""
        return await self._request("DELETE", f"/v1/memory/{memory_id}")
