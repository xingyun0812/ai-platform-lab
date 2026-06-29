"""Gateway 薄 re-export — 实现位于 packages.router（Issue #150）。"""

from packages.router.model_router import (
    ModelRouterConfig,
    ModelRouteResult,
    forward_with_model_router,
    get_model_router_config,
    is_model_allowed,
    reset_model_router_config_for_tests,
    resolve_model_name,
    select_model_with_capability,
)

__all__ = [
    "ModelRouteResult",
    "ModelRouterConfig",
    "forward_with_model_router",
    "get_model_router_config",
    "is_model_allowed",
    "reset_model_router_config_for_tests",
    "resolve_model_name",
    "select_model_with_capability",
]
