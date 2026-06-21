"""Agent resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class AgentResource(BaseResource):
    """Sync agent resource."""

    def run(self, session_id: str, message: str, tools: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/agent/run — run one turn of an agent session."""
        payload: dict[str, Any] = {"session_id": session_id, "message": message, **kwargs}
        if tools is not None:
            payload["tools"] = tools
        return self._request("POST", "/v1/agent/run", json=payload)

    def list_sessions(self) -> list[dict[str, Any]]:
        """GET /v1/agent/sessions."""
        return self._request("GET", "/v1/agent/sessions")

    def get_session(self, session_id: str) -> dict[str, Any]:
        """GET /v1/agent/sessions/{session_id}."""
        return self._request("GET", f"/v1/agent/sessions/{session_id}")


class AsyncAgentResource(AsyncBaseResource):
    """Async agent resource."""

    async def run(self, session_id: str, message: str, tools: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/agent/run."""
        payload: dict[str, Any] = {"session_id": session_id, "message": message, **kwargs}
        if tools is not None:
            payload["tools"] = tools
        return await self._request("POST", "/v1/agent/run", json=payload)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """GET /v1/agent/sessions."""
        return await self._request("GET", "/v1/agent/sessions")

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """GET /v1/agent/sessions/{session_id}."""
        return await self._request("GET", f"/v1/agent/sessions/{session_id}")
