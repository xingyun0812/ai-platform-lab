"""RAG (Retrieval-Augmented Generation) resource."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class RagResource(BaseResource):
    """Sync RAG resource."""

    def query(self, query: str, kb_id: str, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/rag/query — retrieve relevant chunks from a knowledge base."""
        payload: dict[str, Any] = {"query": query, "kb_id": kb_id, "top_k": top_k, **kwargs}
        if self._tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = self._tenant_id
        return self._request("POST", "/v1/rag/query", json=payload)

    def upload(self, kb_id: str, file_path: str | Path, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/rag/upload — upload a document to a knowledge base."""
        file_path = Path(file_path)
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh)}
            # Remove Content-Type so httpx sets multipart boundary automatically
            headers = self._headers()
            headers.pop("Content-Type", None)
            return self._request(
                "POST",
                f"/v1/rag/{kb_id}/upload",
                headers=headers,
                files=files,
                **kwargs,
            )

    def list_kbs(self) -> list[dict[str, Any]]:
        """GET /v1/rag/kbs — list all knowledge bases."""
        return self._request("GET", "/v1/rag/kbs")


class AsyncRagResource(AsyncBaseResource):
    """Async RAG resource."""

    async def query(self, query: str, kb_id: str, top_k: int = 5, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/rag/query."""
        payload: dict[str, Any] = {"query": query, "kb_id": kb_id, "top_k": top_k, **kwargs}
        if self._tenant_id and "tenant_id" not in payload:
            payload["tenant_id"] = self._tenant_id
        return await self._request("POST", "/v1/rag/query", json=payload)

    async def upload(self, kb_id: str, file_path: str | Path, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/rag/{kb_id}/upload."""
        file_path = Path(file_path)
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh)}
            headers = self._headers()
            headers.pop("Content-Type", None)
            return await self._request(
                "POST",
                f"/v1/rag/{kb_id}/upload",
                headers=headers,
                files=files,
                **kwargs,
            )

    async def list_kbs(self) -> list[dict[str, Any]]:
        """GET /v1/rag/kbs."""
        return await self._request("GET", "/v1/rag/kbs")
