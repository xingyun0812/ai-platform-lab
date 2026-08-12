from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from packages.opa import get_opa_client, init_opa_client
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.gateway.opa_middleware")

# 不需要策略检查的路径
_SKIP_PATHS = frozenset({
    "/healthz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
})


class OpaMiddleware(BaseHTTPMiddleware):
    """OPA 策略检查中间件。

    对每个请求构造 OPA input，调用策略引擎评估。
    被拒绝的请求返回 403。
    """

    def __init__(self, app: Any, **kwargs: Any):
        super().__init__(app, **kwargs)
        settings = get_settings()
        if hasattr(settings, "opa_policies_dir") and settings.opa_policies_dir:
            init_opa_client(policies_dir=settings.opa_policies_dir)
            logger.info("OPA middleware initialized: %s", settings.opa_policies_dir)
        else:
            init_opa_client()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()

        # 检查是否启用 OPA
        opa_enabled = getattr(settings, "opa_enabled", False)
        if not opa_enabled:
            return await call_next(request)

        # 跳过无需检查的路径
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        # 构造 OPA input
        tenant_id = request.headers.get("X-Tenant-Id", "")
        role = getattr(request.state, "role", "viewer") if hasattr(request.state, "role") else "viewer"

        opa_input = {
            "tenant_id": tenant_id,
            "role": role,
            "path": request.url.path,
            "method": request.method,
        }

        # 评估策略
        client = get_opa_client()
        if client is not None:
            try:
                result = await client.check(opa_input)
                if not result.get("allow", True):
                    logger.warning(
                        "OPA denied: tenant=%s path=%s method=%s policy=%s reason=%s",
                        tenant_id,
                        request.url.path,
                        request.method,
                        result.get("policy", ""),
                        result.get("reason", ""),
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "POLICY_DENIED",
                            "reason": result.get("reason", "Access denied by policy"),
                            "policy": result.get("policy", ""),
                        },
                    )
            except Exception as exc:
                logger.warning("OPA check failed: %s", exc)

        return await call_next(request)
