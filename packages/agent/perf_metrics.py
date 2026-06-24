"""Agent 运行时性能指标（Phase O #94）。"""

from __future__ import annotations

import threading
from collections import defaultdict


class AgentPerfMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plan_steps: defaultdict[str, int] = defaultdict(int)
        self._parallel_steps: defaultdict[str, int] = defaultdict(int)
        self._cot_thinking_tokens: defaultdict[str, int] = defaultdict(int)
        self._parallel_durations: defaultdict[tuple[str, str], list[float]] = defaultdict(list)

    def record_parallel_steps(self, *, tenant_id: str, steps: int) -> None:
        """记录并行执行的 step 数量 (agent_plan_parallel_steps_total)。"""
        if steps <= 0:
            return
        tenant = tenant_id or "unknown"
        with self._lock:
            self._parallel_steps[tenant] += steps

    def record_plan_steps(self, *, tenant_id: str, steps: int) -> None:
        if steps <= 0:
            return
        tenant = tenant_id or "unknown"
        with self._lock:
            self._plan_steps[tenant] += steps

    def record_cot_thinking_tokens(self, *, tenant_id: str, tokens: int) -> None:
        if tokens <= 0:
            return
        tenant = tenant_id or "unknown"
        with self._lock:
            self._cot_thinking_tokens[tenant] += tokens

    def record_tool_parallel_batch(
        self,
        *,
        tenant_id: str,
        strategy: str,
        duration_ms: float,
        tool_count: int,
    ) -> None:
        if tool_count <= 1:
            return
        tenant = tenant_id or "unknown"
        strat = strategy or "parallel"
        key = (tenant, strat)
        with self._lock:
            bucket = self._parallel_durations[key]
            bucket.append(float(duration_ms))
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = max(0, min(len(sorted_v) - 1, int(0.95 * len(sorted_v)) - 1))
        return sorted_v[idx]

    def prometheus_text(self) -> str:
        with self._lock:
            plan_steps = dict(self._plan_steps)
            parallel_steps = dict(self._parallel_steps)
            cot_tokens = dict(self._cot_thinking_tokens)
            parallel = {k: list(v) for k, v in self._parallel_durations.items()}

        lines: list[str] = []
        lines.append("# HELP agent_plan_steps_total Planner steps executed")
        lines.append("# TYPE agent_plan_steps_total counter")
        for tenant, count in sorted(plan_steps.items()):
            lines.append(f'agent_plan_steps_total{{tenant_id="{tenant}"}} {count}')

        lines.append("# HELP agent_plan_parallel_steps_total Parallel plan steps dispatched")
        lines.append("# TYPE agent_plan_parallel_steps_total counter")
        for tenant, count in sorted(parallel_steps.items()):
            lines.append(f'agent_plan_parallel_steps_total{{tenant_id="{tenant}"}} {count}')

        lines.append("# HELP agent_cot_thinking_tokens CoT thinking token estimate total")
        lines.append("# TYPE agent_cot_thinking_tokens counter")
        for tenant, count in sorted(cot_tokens.items()):
            lines.append(f'agent_cot_thinking_tokens{{tenant_id="{tenant}"}} {count}')

        lines.append("# HELP agent_tool_parallel_duration_ms P95 parallel tool batch wall time")
        lines.append("# TYPE agent_tool_parallel_duration_ms gauge")
        for (tenant, strategy), samples in sorted(parallel.items()):
            p95 = self._p95(samples)
            lines.append(
                f'agent_tool_parallel_duration_ms{{tenant_id="{tenant}",strategy="{strategy}"}} {p95:.2f}'
            )
        return "\n".join(lines) + "\n"


_store: AgentPerfMetrics | None = None


def get_agent_perf_metrics() -> AgentPerfMetrics:
    global _store
    if _store is None:
        _store = AgentPerfMetrics()
    return _store


def reset_agent_perf_metrics_for_tests() -> None:
    global _store
    _store = None
