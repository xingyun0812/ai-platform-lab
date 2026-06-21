"""Base resource helpers for sync and async resources."""
from __future__ import annotations

from typing import Any

import httpx

from ai_platform_lab.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)

_ERROR_MAP = {
    401: AuthenticationError,
    403: AuthenticationError,
    404: NotFoundError,
    429: RateLimitError,
}


def _raise_for_response(response: httpx.Response) -> None:
    """Raise the appropriate SDK exception for a non-2xx response."""
    if response.is_success:
        return
    status = response.status_code
    try:
        body = response.json()
        message = body.get("detail") or body.get("message") or body.get("error") or str(body)
    except Exception:  # noqa: BLE001
        body = response.text
        message = body or f"HTTP {status}"
    exc_cls = _ERROR_MAP.get(status, APIError)
    raise exc_cls(status_code=status, message=message, body=body)


class BaseResource:
    """Sync base resource — holds an ``httpx.Client`` reference."""

    def __init__(self, client: httpx.Client, base_url: str, api_key: str | None, tenant_id: str | None) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._tenant_id:
            headers["X-Tenant-Id"] = self._tenant_id
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._headers()
        headers.update(kwargs.pop("headers", {}))
        response = self._client.request(method, url, headers=headers, **kwargs)
        _raise_for_response(response)
        return response.json()


class AsyncBaseResource:
    """Async base resource — holds an ``httpx.AsyncClient`` reference."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str | None, tenant_id: str | None) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if self._tenant_id:
            headers["X-Tenant-Id"] = self._tenant_id
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._headers()
        headers.update(kwargs.pop("headers", {}))
        response = await self._client.request(method, url, headers=headers, **kwargs)
        _raise_for_response(response)
        return response.json()
