from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from packages.observability.context import get_trace_id

logger = logging.getLogger("ai_platform.observability.otel")

_initialized = False


def init_otel(*, service_name: str, enabled: bool, console_export: bool) -> None:
    """初始化 OpenTelemetry TracerProvider（第 5 周选用 OTel）。"""
    global _initialized
    if not enabled or _initialized:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError as e:
        logger.warning("OpenTelemetry 未安装，跳过 tracing: %s", e)
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    if console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _initialized = True
    logger.info("OpenTelemetry initialized service=%s console=%s", service_name, console_export)


def extract_trace_context_from_headers(headers: dict[str, str]) -> Any:
    """W3C tracecontext 传播：从入站头恢复父 Context。"""
    try:
        from opentelemetry.context import attach, detach
        from opentelemetry.propagate import extract
    except ImportError:
        return None

    carrier = {k.lower(): v for k, v in headers.items()}
    ctx = extract(carrier)
    token = attach(ctx)
    return token


def detach_trace_context(token: Any) -> None:
    if token is None:
        return
    try:
        from opentelemetry.context import detach
    except ImportError:
        return
    detach(token)


@contextmanager
def component_span(
    name: str,
    *,
    component: str,
    enabled: bool,
    **attributes: str | int | float | bool,
) -> Iterator[None]:
    """在 gateway / rag / agent 关键路径打 span，并关联 app.trace_id。"""
    if not enabled:
        yield
        return
    try:
        from opentelemetry import trace
    except ImportError:
        yield
        return

    tracer = trace.get_tracer("ai_platform_lab")
    attrs = {"component": component, **attributes}
    tid = get_trace_id()
    if tid:
        attrs["app.trace_id"] = tid
    with tracer.start_as_current_span(name, attributes=attrs):
        yield
