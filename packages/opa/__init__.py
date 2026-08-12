from __future__ import annotations

import logging
from typing import Any

from packages.opa.evaluator import OpaEvaluator
from packages.opa.loader import OpaLoader

logger = logging.getLogger("ai_platform.opa")

__all__ = [
    "OpaClient",
    "OpaLoader",
    "OpaEvaluator",
    "get_opa_client",
    "init_opa_client",
    "reset_opa_for_tests",
]

_client: OpaClient | None = None


class OpaClient:
    """OPA 客户端 — 策略评估入口。"""

    def __init__(self, policies_dir: str = "config/policies"):
        self._loader = OpaLoader(policies_dir=policies_dir)
        self._evaluator = OpaEvaluator(loader=self._loader)

    async def check(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """评估策略输入。"""
        return await self._evaluator.check(input_data)

    async def is_allowed(self, input_data: dict[str, Any]) -> bool:
        """快捷方法：检查是否允许。"""
        result = await self.check(input_data)
        return result.get("allow", True)

    def reload_policies(self) -> None:
        self._loader.load_all()


# Singleton 管理


def init_opa_client(policies_dir: str = "config/policies") -> OpaClient:
    global _client
    _client = OpaClient(policies_dir=policies_dir)
    return _client


def get_opa_client() -> OpaClient | None:
    return _client


def reset_opa_for_tests() -> None:
    global _client
    _client = None
