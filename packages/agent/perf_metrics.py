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
        self._self_evolve_experiences: defaultdict[str, int] = defaultdict(int)
        self._self_evolve_strategy_patches: defaultdict[str, int] = defaultdict(int)
        # Reflection metrics (#256)
        self._reflection_uses: defaultdict[str, int] = defaultdict(int)  # "reason|depth" -> count
        self._reflection_tokens: defaultdict[str, int] = defaultdict(
            int
        )  # "reason|depth" -> tokens
        self._reflection_latency_ms: defaultdict[str, float] = defaultdict(float)
        self._reflection_rounds: defaultdict[str, int] = defaultdict(int)
        # Guardrail metrics
        self._guardrail_triggered: dict = defaultdict(int)  # "layer|reason" -> count
        self._guardrail_stuck: dict = defaultdict(int)  # "reason" -> count

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

    def record_self_evolve_experience(self, tenant_id: str) -> None:
        """记录一次经验沉淀 (agent_self_evolve_experiences_total)。"""
        tenant = tenant_id or "unknown"
        with self._lock:
            self._self_evolve_experiences[tenant] += 1

    def record_self_evolve_strategy_patch(self, tenant_id: str) -> None:
        """记录一次策略 patch 提案 (agent_self_evolve_strategy_patches_total)。"""
        tenant = tenant_id or "unknown"
        with self._lock:
            self._self_evolve_strategy_patches[tenant] += 1

    def record_reflection_use(
        self,
        *,
        reason: str,
        depth: str,
        tokens: int = 0,
        latency_ms: float = 0.0,
        rounds: int = 0,
    ) -> None:
        """记录一次反思使用 (agent_reflection_uses_total / tokens / latency / rounds)。

        用于 #256 反思成本治理的可观测性。按 reason|depth 聚合。
        """
        key = f"{reason}|{depth}"
        with self._lock:
            self._reflection_uses[key] += 1
            self._reflection_tokens[key] += int(tokens)
            self._reflection_latency_ms[key] += float(latency_ms)
            self._reflection_rounds[key] += int(rounds)

    def record_guardrail_triggered(self, *, layer: int, reason: str) -> None:
        """记录 guardrail 触发次数 (guardrail_triggered_total)。"""
        key = f"{layer}|{reason}"
        with self._lock:
            self._guardrail_triggered[key] += 1

    def record_guardrail_stuck(self, *, reason: str) -> None:
        """记录 stuck 检测次数 (guardrail_stuck_total)。"""
        key = reason or "unknown"
        with self._lock:
            self._guardrail_stuck[key] += 1

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
            se_experiences = dict(self._self_evolve_experiences)
            se_patches = dict(self._self_evolve_strategy_patches)
            refl_uses = dict(self._reflection_uses)
            refl_tokens = dict(self._reflection_tokens)
            refl_latency = dict(self._reflection_latency_ms)
            refl_rounds = dict(self._reflection_rounds)
            gr_triggered = dict(self._guardrail_triggered)
            gr_stuck = dict(self._guardrail_stuck)

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
        lines.append("# HELP agent_self_evolve_experiences_total Total experiences stored")
        lines.append("# TYPE agent_self_evolve_experiences_total counter")
        for tenant, count in sorted(se_experiences.items()):
            lines.append(f'agent_self_evolve_experiences_total{{tenant="{tenant}"}} {count}')

        lines.append(
            "# HELP agent_self_evolve_strategy_patches_total Total strategy patches proposed"
        )
        lines.append("# TYPE agent_self_evolve_strategy_patches_total counter")
        for tenant, count in sorted(se_patches.items()):
            lines.append(f'agent_self_evolve_strategy_patches_total{{tenant="{tenant}"}} {count}')

        lines.append(
            "# HELP agent_reflection_uses_total Reflection gate decisions by reason and depth"
        )
        lines.append("# TYPE agent_reflection_uses_total counter")
        for key, count in sorted(refl_uses.items()):
            reason = key.split("|", 1)[0] if "|" in key else key
            depth = key.split("|", 1)[1] if "|" in key else "unknown"
            lines.append(
                f'agent_reflection_uses_total{{reason="{reason}",depth="{depth}"}} {count}'
            )

        lines.append(
            "# HELP agent_reflection_tokens_total Reflection LLM tokens by reason and depth"
        )
        lines.append("# TYPE agent_reflection_tokens_total counter")
        for key, count in sorted(refl_tokens.items()):
            reason = key.split("|", 1)[0] if "|" in key else key
            depth = key.split("|", 1)[1] if "|" in key else "unknown"
            lines.append(
                f'agent_reflection_tokens_total{{reason="{reason}",depth="{depth}"}} {count}'
            )

        lines.append(
            "# HELP agent_reflection_latency_ms_total Reflection latency by reason and depth"
        )
        lines.append("# TYPE agent_reflection_latency_ms_total counter")
        for key, value in sorted(refl_latency.items()):
            reason = key.split("|", 1)[0] if "|" in key else key
            depth = key.split("|", 1)[1] if "|" in key else "unknown"
            lines.append(
                f'agent_reflection_latency_ms_total{{reason="{reason}",depth="{depth}"}} {value:.2f}'
            )

        lines.append("# HELP agent_reflection_rounds_total Convergence rounds by reason and depth")
        lines.append("# TYPE agent_reflection_rounds_total counter")
        for key, count in sorted(refl_rounds.items()):
            reason = key.split("|", 1)[0] if "|" in key else key
            depth = key.split("|", 1)[1] if "|" in key else "unknown"
            lines.append(
                f'agent_reflection_rounds_total{{reason="{reason}",depth="{depth}"}} {count}'
            )

        lines.append("# HELP guardrail_triggered_total Guardrail trigger count by layer and reason")
        lines.append("# TYPE guardrail_triggered_total counter")
        for key, count in sorted(gr_triggered.items()):
            parts = key.split("|", 1)
            layer = parts[0]
            reason = parts[1] if len(parts) > 1 else "unknown"
            lines.append(f'guardrail_triggered_total{{layer="{layer}",reason="{reason}"}} {count}')

        lines.append("# HELP guardrail_stuck_total Agent stuck detection count by reason")
        lines.append("# TYPE guardrail_stuck_total counter")
        for key, count in sorted(gr_stuck.items()):
            lines.append(f'guardrail_stuck_total{{reason="{key}"}} {count}')

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
