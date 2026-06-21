"""Orchestrator resource."""
from __future__ import annotations

from typing import Any

from ai_platform_lab._base import AsyncBaseResource, BaseResource


class OrchestratorResource(BaseResource):
    """Sync orchestrator resource."""

    def create_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/orchestrator/workflows — create a workflow definition."""
        return self._request("POST", "/v1/orchestrator/workflows", json=workflow)

    def list_workflows(self) -> list[dict[str, Any]]:
        """GET /v1/orchestrator/workflows."""
        return self._request("GET", "/v1/orchestrator/workflows")

    def execute(self, workflow_id: str, inputs: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/orchestrator/workflows/{workflow_id}/execute."""
        payload: dict[str, Any] = {"inputs": inputs or {}, **kwargs}
        return self._request("POST", f"/v1/orchestrator/workflows/{workflow_id}/execute", json=payload)

    def delete_workflow(self, workflow_id: str) -> dict[str, Any]:
        """DELETE /v1/orchestrator/workflows/{workflow_id}."""
        return self._request("DELETE", f"/v1/orchestrator/workflows/{workflow_id}")


class AsyncOrchestratorResource(AsyncBaseResource):
    """Async orchestrator resource."""

    async def create_workflow(self, workflow: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/orchestrator/workflows."""
        return await self._request("POST", "/v1/orchestrator/workflows", json=workflow)

    async def list_workflows(self) -> list[dict[str, Any]]:
        """GET /v1/orchestrator/workflows."""
        return await self._request("GET", "/v1/orchestrator/workflows")

    async def execute(self, workflow_id: str, inputs: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """POST /v1/orchestrator/workflows/{workflow_id}/execute."""
        payload: dict[str, Any] = {"inputs": inputs or {}, **kwargs}
        return await self._request("POST", f"/v1/orchestrator/workflows/{workflow_id}/execute", json=payload)

    async def delete_workflow(self, workflow_id: str) -> dict[str, Any]:
        """DELETE /v1/orchestrator/workflows/{workflow_id}."""
        return await self._request("DELETE", f"/v1/orchestrator/workflows/{workflow_id}")
