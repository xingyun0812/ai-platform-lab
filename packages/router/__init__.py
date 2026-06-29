"""packages.router — 模型路由（别名、降级链、能力感知选模）。"""

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
