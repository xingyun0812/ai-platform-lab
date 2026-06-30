"""Gateway FastAPI lifespan — startup/shutdown 边界（Issue #156 PR-2）。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.gateway.composition import wire_gateway_dependencies
from apps.gateway.platform_adapter import wire_platform
from apps.gateway.settings import get_settings

logger = logging.getLogger("ai_platform.gateway.lifespan")


@asynccontextmanager
async def gateway_lifespan(_app: FastAPI):
    """应用启动时绑定 PlatformPort 并初始化 Phase 依赖；关闭时记录日志。"""
    settings = get_settings()
    wire_platform()
    wire_gateway_dependencies(settings)
    logger.info("gateway lifespan startup complete app=%s", settings.app_name)
    try:
        yield
    finally:
        logger.info("gateway lifespan shutdown app=%s", settings.app_name)
