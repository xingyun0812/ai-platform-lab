"""Gateway 入口 — create_app 编排 only（Issue #156 PR-1）。"""

from __future__ import annotations

from fastapi import FastAPI

from apps.gateway.chat_routes import register_chat_routes
from apps.gateway.composition import wire_gateway_dependencies
from apps.gateway.core_routes import register_core_routes
from apps.gateway.http_middleware import register_gateway_middleware
from apps.gateway.platform_adapter import wire_platform
from apps.gateway.router_registry import mount_gateway_routers
from apps.gateway.settings import get_settings
from packages.observability.middleware import TraceIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    wire_platform()
    wire_gateway_dependencies(settings)

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.add_middleware(TraceIdMiddleware)
    register_gateway_middleware(app, settings)
    mount_gateway_routers(app)
    register_core_routes(app, settings)
    register_chat_routes(app)
    return app


app = create_app()
