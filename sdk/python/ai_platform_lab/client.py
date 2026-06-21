"""Main client — sync and async entry points."""
from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from ai_platform_lab.resources.agent import AgentResource, AsyncAgentResource
from ai_platform_lab.resources.chat import AsyncChatResource, ChatResource
from ai_platform_lab.resources.embedding import AsyncEmbeddingResource, EmbeddingResource
from ai_platform_lab.resources.memory import AsyncMemoryResource, MemoryResource
from ai_platform_lab.resources.orchestrator import AsyncOrchestratorResource, OrchestratorResource
from ai_platform_lab.resources.rag import AsyncRagResource, RagResource


class Client:
    """Synchronous AI Platform Lab client.

    Usage::

        client = Client(base_url="http://localhost:8000", api_key="my-key")
        resp = client.chat.completions.create(model="gpt-4o", messages=[...])
        client.close()

        # or as a context manager
        with Client(base_url="...", api_key="...") as client:
            resp = client.chat.completions.create(...)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        tenant_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id
        self._http = httpx.Client(timeout=timeout)

    # ------------------------------------------------------------------ #
    # Resource properties
    # ------------------------------------------------------------------ #

    @property
    def chat(self) -> ChatResource:
        return ChatResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def rag(self) -> RagResource:
        return RagResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def agent(self) -> AgentResource:
        return AgentResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def embedding(self) -> EmbeddingResource:
        return EmbeddingResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def memory(self) -> MemoryResource:
        return MemoryResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def orchestrator(self) -> OrchestratorResource:
        return OrchestratorResource(self._http, self._base_url, self._api_key, self._tenant_id)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncClient:
    """Asynchronous AI Platform Lab client.

    Usage::

        async with AsyncClient(base_url="http://localhost:8000", api_key="key") as client:
            resp = await client.chat.completions.create(model="gpt-4o", messages=[...])
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        tenant_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id
        self._http = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------ #
    # Resource properties
    # ------------------------------------------------------------------ #

    @property
    def chat(self) -> AsyncChatResource:
        return AsyncChatResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def rag(self) -> AsyncRagResource:
        return AsyncRagResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def agent(self) -> AsyncAgentResource:
        return AsyncAgentResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def embedding(self) -> AsyncEmbeddingResource:
        return AsyncEmbeddingResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def memory(self) -> AsyncMemoryResource:
        return AsyncMemoryResource(self._http, self._base_url, self._api_key, self._tenant_id)

    @property
    def orchestrator(self) -> AsyncOrchestratorResource:
        return AsyncOrchestratorResource(self._http, self._base_url, self._api_key, self._tenant_id)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        """Close the underlying async HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
