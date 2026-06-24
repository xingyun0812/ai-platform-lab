"""Phase O #94 — Agent 性能：并行工具、metrics、长上下文策略。"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from apps.gateway.settings import Settings
from packages.agent.context_budget import (
    assemble_llm_messages,
    context_strategy_platform_meta,
    estimate_messages_tokens,
)
from packages.agent.perf_metrics import get_agent_perf_metrics, reset_agent_perf_metrics_for_tests
from packages.agent.registry import ToolRegistry
from packages.agent.session import SessionStore
from packages.agent.session_state import SessionState
from packages.agent.tool_strategy import resolve_tool_call_strategy
from packages.agent.tools.base import ToolDefinition


def _run(coro):
    return asyncio.run(coro)


def _dual_tool_calls() -> list[dict]:
    return [
        {
            "id": "call_a",
            "type": "function",
            "function": {"name": "slow_a", "arguments": "{}"},
        },
        {
            "id": "call_b",
            "type": "function",
            "function": {"name": "slow_b", "arguments": "{}"},
        },
    ]


class ToolCallStrategyTests(unittest.TestCase):
    def test_resolve_defaults_to_sequential(self) -> None:
        self.assertEqual(resolve_tool_call_strategy(None, None), "sequential")

    def test_resolve_parallel(self) -> None:
        self.assertEqual(resolve_tool_call_strategy("parallel", "sequential"), "parallel")

    def test_invalid_strategy_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_tool_call_strategy("burst", None)


class ContextStrategyTests(unittest.TestCase):
    def test_strategy_meta_documents_rag_separation(self) -> None:
        meta = context_strategy_platform_meta()
        self.assertIn("memory_injection", meta)
        self.assertIn("rag_references", meta)

    def test_assemble_pins_summary_prefix(self) -> None:
        state = SessionState(
            messages=[{"role": "user", "content": "hi"}],
            summary="older context",
            turn_count=1,
        )
        combined, budget_meta = assemble_llm_messages(
            state,
            [{"role": "user", "content": "new"}],
            budget=8000,
            keep_recent_turns=4,
            tool_result_max_chars=2000,
        )
        self.assertTrue(budget_meta.summary_applied)
        self.assertTrue(combined[0]["content"].startswith("[session_summary]"))
        self.assertGreater(estimate_messages_tokens(combined), 0)


class AgentPerfMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def tearDown(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def test_prometheus_export_contains_agent_metrics(self) -> None:
        metrics = get_agent_perf_metrics()
        metrics.record_plan_steps(tenant_id="admin", steps=2)
        metrics.record_cot_thinking_tokens(tenant_id="admin", tokens=42)
        metrics.record_tool_parallel_batch(
            tenant_id="admin",
            strategy="parallel",
            duration_ms=12.5,
            tool_count=2,
        )
        text = metrics.prometheus_text()
        self.assertIn("agent_plan_steps_total", text)
        self.assertIn("agent_cot_thinking_tokens", text)
        self.assertIn("agent_tool_parallel_duration_ms", text)
        self.assertIn('tenant_id="admin"', text)


class ParallelToolExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def tearDown(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def _build_registry(self) -> ToolRegistry:
        delay = 0.12

        async def slow_a(_args: dict) -> str:
            await asyncio.sleep(delay)
            return json.dumps({"ok": True, "data": "a"})

        async def slow_b(_args: dict) -> str:
            await asyncio.sleep(delay)
            return json.dumps({"ok": True, "data": "b"})

        tools = {
            "slow_a": ToolDefinition(
                name="slow_a",
                description="slow a",
                parameters_schema={"type": "object"},
                handler=slow_a,
            ),
            "slow_b": ToolDefinition(
                name="slow_b",
                description="slow b",
                parameters_schema={"type": "object"},
                handler=slow_b,
            ),
        }
        return ToolRegistry(tools=tools)

    def test_process_tool_calls_parallel_faster_than_sequential(self) -> None:
        from packages.agent.runner import _process_tool_calls_round

        settings = Settings()
        reg = self._build_registry()
        tool_calls = _dual_tool_calls()

        async def run_batch(strategy: str) -> float:
            reset_agent_perf_metrics_for_tests()
            local_trace: list = []
            local_shadow: list = []
            t0 = time.perf_counter()
            result = await _process_tool_calls_round(
                tool_calls,
                reg=reg,
                allowed_tools=("slow_a", "slow_b"),
                settings=settings,
                tenant_id="admin",
                session_id="batch",
                shadow_mode=False,
                strategy=strategy,
                reflect_remaining=0,
                runtime_truncated_tools=0,
                trace=local_trace,
                shadow_trace=local_shadow,
            )
            self.assertIsNone(result.fatal)
            self.assertEqual(len(result.tool_messages), 2)
            return time.perf_counter() - t0

        seq_elapsed = _run(run_batch("sequential"))
        par_elapsed = _run(run_batch("parallel"))
        self.assertLess(par_elapsed, seq_elapsed * 0.75)
        prom = get_agent_perf_metrics().prometheus_text()
        self.assertIn("agent_tool_parallel_duration_ms", prom)

    @patch("apps.gateway.model_router.forward_with_model_router", new_callable=AsyncMock)
    @patch("apps.gateway.settings.get_settings")
    def test_run_agent_reports_tool_call_strategy(self, mock_settings, mock_route) -> None:
        from packages.agent.runner import run_agent

        settings = Settings()
        settings.agent_tool_call_strategy = "parallel"
        mock_settings.return_value = settings

        class R:
            status = 200
            body = {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }
            model_used = "chat-fast"
            error = None

        mock_route.return_value = R()

        async def _go():
            return await run_agent(
                tenant_id="admin",
                session_id="strategy-meta",
                new_messages=[{"role": "user", "content": "hi"}],
                allowed_tools=("calc",),
                allowed_models=("chat-fast",),
                model="chat-fast",
                session_store=SessionStore(),
                tool_call_strategy="parallel",
            )

        result = _run(_go())
        self.assertEqual(result.get("tool_call_strategy"), "parallel")
        platform = result.get("_platform") or {}
        self.assertEqual(platform.get("tool_call_strategy"), "parallel")
        self.assertIn("context_strategy", platform)


if __name__ == "__main__":
    unittest.main()
