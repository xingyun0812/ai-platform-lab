from __future__ import annotations

from contextvars import ContextVar

_current_region: ContextVar[str | None] = ContextVar("current_region", default=None)
_current_qdrant_url: ContextVar[str | None] = ContextVar("current_qdrant_url", default=None)


def set_request_region(*, region_id: str, qdrant_url: str) -> None:
    _current_region.set(region_id)
    _current_qdrant_url.set(qdrant_url)


def get_request_region() -> str | None:
    return _current_region.get()


def get_request_qdrant_url() -> str | None:
    return _current_qdrant_url.get()


def clear_request_region() -> None:
    _current_region.set(None)
    _current_qdrant_url.set(None)
