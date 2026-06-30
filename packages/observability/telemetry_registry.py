"""TelemetryRegistry — 统一 Prometheus 指标聚合（架构 §10 / Issue #186）。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from packages.observability.metrics import get_metrics_store

logger = logging.getLogger("ai_platform.observability.telemetry_registry")

PrometheusCollector = Callable[[], str]

_collectors: list[tuple[str, PrometheusCollector]] = []
_bootstrapped = False


def register_prometheus_collector(name: str, loader: PrometheusCollector) -> None:
    """注册指标 collector；同名重复注册时后者覆盖前者。"""
    global _collectors
    for idx, (existing, _) in enumerate(_collectors):
        if existing == name:
            _collectors[idx] = (name, loader)
            return
    _collectors.append((name, loader))


def reset_telemetry_registry_for_tests() -> None:
    """测试 teardown：清空 collector 与 bootstrap 标记。"""
    global _collectors, _bootstrapped
    _collectors = []
    _bootstrapped = False


def bootstrap_default_collectors() -> None:
    """注册 gateway 默认 collector（幂等）。"""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True

    register_prometheus_collector(
        "semantic_cache",
        lambda: __import__("packages.semantic_cache", fromlist=["get_semantic_cache_metrics"])
        .get_semantic_cache_metrics()
        .prometheus_text(),
    )
    register_prometheus_collector(
        "memory",
        lambda: __import__("packages.memory", fromlist=["get_memory_metrics"])
        .get_memory_metrics()
        .prometheus_text(),
    )
    register_prometheus_collector(
        "rag_index",
        lambda: __import__(
            "packages.rag.index_metrics", fromlist=["get_index_metrics"]
        ).get_index_metrics().prometheus_text(),
    )
    register_prometheus_collector(
        "agent_perf",
        lambda: __import__(
            "packages.agent.perf_metrics", fromlist=["get_agent_perf_metrics"]
        ).get_agent_perf_metrics().prometheus_text(),
    )


def prometheus_text(*, include_core: bool = True) -> str:
    """聚合 core MetricsStore + 已注册 collector 的 Prometheus 文本。"""
    bootstrap_default_collectors()
    parts: list[str] = []
    if include_core:
        parts.append(get_metrics_store().prometheus_text())
    for name, loader in _collectors:
        try:
            parts.append(loader())
        except Exception:
            logger.exception("telemetry collector %s export failed", name)
    return "".join(parts)
