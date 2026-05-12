import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages.observability.context import bind_trace_id, reset_trace_id

logger = logging.getLogger("ai_platform.observability")


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 trace_id：优先 X-Request-Id，否则生成 UUID。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("x-request-id") or request.headers.get("X-Request-Id")
        trace_id = incoming.strip() if incoming else str(uuid.uuid4())
        token = bind_trace_id(trace_id)
        try:
            response = await call_next(request)
        finally:
            reset_trace_id(token)
        response.headers["X-Request-Id"] = trace_id
        return response
