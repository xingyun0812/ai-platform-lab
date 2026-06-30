"""Gateway AppContext — 统一 init/get 单例装配（Issue #178 / 架构 §8）。

构造期注入 Settings，``wire()`` 按 feature flag 初始化各包全局 store。
测试用 ``AppContext.test()`` 或 ``reset_all_for_tests()`` 一次性清理。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apps.gateway.composition import wire_gateway_dependencies

if TYPE_CHECKING:
    from apps.gateway.settings import Settings

logger = logging.getLogger("ai_platform.gateway.app_context")


@dataclass
class AppContext:
    """Gateway 进程内依赖容器。

    字段
    ----
    settings
        来自 ``get_settings()`` 或测试 override。
    wired
        ``wire()`` 是否已执行；重复调用 ``wire()`` 为 no-op。
    """

    settings: Settings
    wired: bool = False

    def wire(self) -> None:
        """按 settings feature flag 初始化各包 ``init_*`` 单例。"""
        if self.wired:
            logger.debug("AppContext.wire skipped (already wired)")
            return
        wire_gateway_dependencies(self.settings)
        self.wired = True
        logger.info("AppContext wired app=%s", self.settings.app_name)

    @classmethod
    def test(cls, **settings_overrides: object) -> AppContext:
        """测试工厂：reset 全局单例后返回可 ``wire()`` 的 context。"""
        from apps.gateway.settings import Settings, get_settings

        reset_all_for_tests()
        base = get_settings()
        if settings_overrides:
            data = base.model_dump()
            data.update(settings_overrides)
            settings = Settings(**data)
        else:
            settings = base
        return cls(settings=settings)


def build_app_context(settings: Settings) -> AppContext:
    """从 Settings 构造 AppContext（不自动 wire）。"""
    return AppContext(settings=settings)


def reset_all_for_tests() -> None:
    """重置 ``wire()`` 可能触达的全局单例（测试 teardown 入口）。"""
    from packages.platform import reset_platform_for_tests

    reset_platform_for_tests()

    _safe_reset("packages.semantic_cache.store", "reset_semantic_cache_for_tests")
    _safe_reset("packages.prompt.registry", "reset_registry_for_tests")
    _safe_reset("packages.prompt.experiment", "reset_experiment_store_for_tests")
    _safe_reset("packages.memory.store", "reset_memory_store_for_tests")
    _safe_reset("packages.mcp.registry", "reset_mcp_registry_for_tests")
    _safe_reset("packages.agent.orchestrator.workflow_store", "reset_workflow_store_for_tests")
    _safe_reset("packages.agent.multi_agent.registry", "reset_agent_registry_for_tests")
    _safe_reset("packages.agent.lifecycle.registry", "reset_lifecycle_registry_for_tests")
    _safe_reset("packages.hitl.store", "reset_approval_store_for_tests")
    _safe_reset("packages.embedding.service", "reset_embedding_service_for_tests")
    _safe_reset("packages.sandbox.executor", "reset_sandbox_for_tests")
    _safe_reset("packages.audit.action_levels", "reset_for_tests")
    _safe_reset("packages.audit.action_logger", "reset_for_tests")
    _safe_reset("packages.pii.service", "reset_for_tests")
    _safe_reset("packages.auth.oauth2", "reset_for_tests")
    _safe_reset("packages.auth.mtls", "reset_for_tests")
    _safe_reset("packages.storage.factory", "reset_for_tests")
    _safe_reset("packages.feedback.store", "reset_for_tests")
    _safe_reset("packages.quality_monitor.aggregator", "reset_for_tests")
    _safe_reset("packages.feedback_loop.pipeline", "reset_for_tests")


def _safe_reset(module_path: str, func_name: str) -> None:
    import importlib

    try:
        mod = importlib.import_module(module_path)
        reset_fn = getattr(mod, func_name, None)
        if reset_fn is not None:
            reset_fn()
    except Exception as exc:
        logger.debug("reset_all skip %s.%s: %s", module_path, func_name, exc)
