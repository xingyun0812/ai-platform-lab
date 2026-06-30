"""Gateway 单测共用 — 触发 FastAPI lifespan 的 TestClient。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from apps.gateway.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator


class LifespanTestClient(AbstractContextManager[TestClient]):
    """包装 TestClient，enter 时执行 lifespan startup。"""

    def __init__(self) -> None:
        self._ctx = TestClient(create_app())

    def __enter__(self) -> TestClient:
        return self._ctx.__enter__()

    def __exit__(self, *exc) -> None:
        self._ctx.__exit__(*exc)


def open_gateway_test_client() -> Iterator[TestClient]:
    with LifespanTestClient() as client:
        yield client
