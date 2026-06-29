import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages.platform import get_settings
from packages.observability.context import bind_trace_id, reset_trace_id
from packages.observability.metrics import get_metrics_store
from packages.observability.otel import (
    component_span,
    detach_trace_context,
    extract_trace_context_from_headers,
)

logger = logging.getLogger("ai_platform.observability")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """trace_id +（可选）OpenTelemetry 根 span + 指标采样。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        headers = {k: v for k, v in request.headers.items()}
        otel_token = extract_trace_context_from_headers(headers)

        incoming = request.headers.get("x-request-id") or request.headers.get("X-Request-Id")
        trace_id = incoming.strip() if incoming else str(uuid.uuid4())
        bind_token = bind_trace_id(trace_id)
        start = time.perf_counter()
        tenant_id = request.headers.get("x-tenant-id") or request.headers.get("X-Tenant-Id") or ""
        status_code = 500
        response: Response | None = None

        try:
            with component_span(
                "http.request",
                component="gateway",
                enabled=settings.otel_enabled,
                **{
                    "http.method": request.method,
                    "http.route": request.url.path,
                },
            ):
                response = await call_next(request)
                status_code = response.status_code
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if settings.metrics_enabled:
                get_metrics_store().record_request(
                    path=request.url.path,
                    tenant_id=tenant_id.strip() if tenant_id else "unknown",
                    status_code=status_code,
                    latency_ms=elapsed_ms,
                )
            reset_trace_id(bind_token)
            detach_trace_context(otel_token)

        assert response is not None
        response.headers["X-Request-Id"] = trace_id
        return response
