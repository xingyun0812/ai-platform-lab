"""Embedding resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource

_EMBED_PATH = "/internal/embeddings/embed"
_MODELS_PATH = "/internal/embeddings/models"


def _extract_models(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("models"), list):
        return body["models"]
    if isinstance(body, list):
        return body
    return []


class EmbeddingResource(BaseResource):
    """Sync embedding resource."""

    def create(
        self,
        model_id: str,
        texts: list[str] | None = None,
        *,
        inputs: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST /internal/embeddings/embed — texts 或 inputs 二选一。"""
        payload: dict[str, Any] = {"model_id": model_id, **kwargs}
        if inputs is not None:
            payload["inputs"] = inputs
        elif texts is not None:
            payload["texts"] = texts
        else:
            raise ValueError("texts 或 inputs 至少提供一个")
        return self._request("POST", _EMBED_PATH, json=payload)

    def create_with_inputs(
        self,
        model_id: str,
        inputs: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """多模态 embed：inputs 为 text / image_url / image_base64 列表。"""
        return self.create(model_id, inputs=inputs, **kwargs)

    def list_models(self) -> list[dict[str, Any]]:
        """GET /internal/embeddings/models — 列出 embedding 模型。"""
        body = self._request("GET", _MODELS_PATH)
        return _extract_models(body)


class AsyncEmbeddingResource(AsyncBaseResource):
    """Async embedding resource."""

    async def create(
        self,
        model_id: str,
        texts: list[str] | None = None,
        *,
        inputs: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """POST /internal/embeddings/embed."""
        payload: dict[str, Any] = {"model_id": model_id, **kwargs}
        if inputs is not None:
            payload["inputs"] = inputs
        elif texts is not None:
            payload["texts"] = texts
        else:
            raise ValueError("texts 或 inputs 至少提供一个")
        return await self._request("POST", _EMBED_PATH, json=payload)

    async def create_with_inputs(
        self,
        model_id: str,
        inputs: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """多模态 embed。"""
        return await self.create(model_id, inputs=inputs, **kwargs)

    async def list_models(self) -> list[dict[str, Any]]:
        """GET /internal/embeddings/models."""
        body = await self._request("GET", _MODELS_PATH)
        return _extract_models(body)
