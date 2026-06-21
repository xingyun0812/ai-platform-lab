"""Embedding resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class EmbeddingResource(BaseResource):
    """Sync embedding resource."""

    def create(self, model: str, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """POST /v1/embeddings — create embeddings for a list of texts."""
        payload = {"model": model, "input": texts, **kwargs}
        return self._request("POST", "/v1/embeddings", json=payload)

    def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/embeddings/models — list available embedding models."""
        return self._request("GET", "/v1/embeddings/models")


class AsyncEmbeddingResource(AsyncBaseResource):
    """Async embedding resource."""

    async def create(self, model: str, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """POST /v1/embeddings."""
        payload = {"model": model, "input": texts, **kwargs}
        return await self._request("POST", "/v1/embeddings", json=payload)

    async def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/embeddings/models."""
        return await self._request("GET", "/v1/embeddings/models")
