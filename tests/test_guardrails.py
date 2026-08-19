#!/usr/bin/env python3
"""Phase Y: Guardrails 单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import pytest

from packages.agent.guardrails import (
    AgentGuardrailConfig,
    GuardrailVerdict,
    ProgressTracker,
    StuckVerdict,
    ThresholdEnforcer,
    check_convergence,
)
from packages.agent.perf_metrics import (
    get_agent_perf_metrics,
    reset_agent_perf_metrics_for_tests,
)


class TestAgentGuardrailConfig:
    """Y1: AgentGuardrailConfig 参数校验。"""

    def test_default_config(self):
        cfg = AgentGuardrailConfig()
        assert cfg.plan_max_steps == 20
        assert cfg.plan_max_depth == 3
        assert cfg.convergence_enabled
        assert cfg.convergence_strategy == "hybrid"
        assert cfg.convergence_threshold == 0.85
        assert cfg.progress_check_enabled
        assert cfg.max_consecutive_empty_tools == 3
        assert cfg.max_consecutive_identical_calls == 3
        assert cfg.max_tool_calls_total == 30
        assert cfg.agent_timeout_seconds == 300.0
        assert cfg.enabled

    def test_tool_call_limits_default(self):
        cfg = AgentGuardrailConfig()
        assert cfg.tool_call_limits["web_search"] == 5
        assert cfg.tool_call_limits["sql_query"] == 10
        assert cfg.tool_call_limits["computer_use"] == 20

    def test_to_dict(self):
        cfg = AgentGuardrailConfig(plan_max_steps=10, convergence_strategy="similarity")
        d = cfg.to_dict()
        assert d["plan_max_steps"] == 10
        assert d["convergence_strategy"] == "similarity"
        assert d["enabled"]
        assert isinstance(d["tool_call_limits"], dict)

    def test_disabled(self):
        cfg = AgentGuardrailConfig(enabled=False)
        assert not cfg.enabled


class TestConvergenceSimilarity(unittest.IsolatedAsyncioTestCase):
    """Y1: check_convergence similarity 策略（mock embed_texts）。"""

    @patch("packages.rag.embeddings.embed_texts")
    async def test_converged_high_similarity(self, mock_embed: AsyncMock):
        mock_embed.return_value = [[1.0, 0.0], [1.0, 0.0]]
        converged, reason = await check_convergence(
            strategy="similarity",
            current_output="hello world",
            previous_output="hello world",
            threshold=0.85,
        )
        assert converged
        assert reason == "similarity"

    @patch("packages.rag.embeddings.embed_texts")
    async def test_not_converged_low_similarity(self, mock_embed: AsyncMock):
        mock_embed.return_value = [[1.0, 0.0], [0.0, 1.0]]
        converged, reason = await check_convergence(
            strategy="similarity",
            current_output="foo",
            previous_output="bar",
            threshold=0.85,
        )
        assert not converged

    async def test_no_previous_output(self):
        converged, reason = await check_convergence(
            strategy="similarity",
            current_output="hello",
            previous_output=None,
        )
        assert not converged
        assert reason == "similarity_no_previous"


class TestProgressTracker:
    """Y1: ProgressTracker 进度校验。"""

    def test_empty_tool_detection(self):
        tracker = ProgressTracker(max_consecutive_empty_tools=2)
        tracker.record_tool_call("web_search", result="")
        tracker.record_tool_call("web_search", result=None)
        verdict = tracker.check_stuck()
        assert verdict is not None
        assert verdict.stuck
        assert verdict.reason == "empty_tools"

    def test_identical_call_detection(self):
        tracker = ProgressTracker(max_consecutive_identical_calls=3)
        args = {"query": "same"}
        tracker.record_tool_call("web_search", args, result="some result")
        tracker.record_tool_call("web_search", args, result="some result")
        tracker.record_tool_call("web_search", args, result="some result")
        verdict = tracker.check_stuck()
        assert verdict is not None
        assert verdict.stuck
        assert verdict.reason == "identical_calls"
        assert verdict.detail is not None
        assert verdict.detail["tool"] == "web_search"

    def test_returns_none_when_not_stuck(self):
        tracker = ProgressTracker()
        tracker.record_tool_call("web_search", result="result_a")
        tracker.record_tool_call("sql_query", result="result_b")
        assert tracker.check_stuck() is None

    def test_empty_history_returns_none(self):
        tracker = ProgressTracker()
        assert tracker.check_stuck() is None

    def test_no_tool_rounds_property(self):
        tracker = ProgressTracker()
        assert tracker.consecutive_no_tool_rounds == 0
        tracker.record_no_tool_call()
        assert tracker.consecutive_no_tool_rounds == 1

    def test_total_tool_calls(self):
        tracker = ProgressTracker()
        assert tracker.total_tool_calls == 0
        tracker.record_tool_call("web_search")
        assert tracker.total_tool_calls == 1
        tracker.record_tool_call("sql_query")
        assert tracker.total_tool_calls == 2

    def test_recent_llm_outputs(self):
        tracker = ProgressTracker(window_size=3)
        tracker.record_llm_output("out1")
        tracker.record_llm_output("out2")
        assert tracker.recent_llm_outputs == ["out1", "out2"]


class TestThresholdEnforcer:
    """Y1: ThresholdEnforcer 阈值熔断。"""

    def test_total_limit(self):
        enforcer = ThresholdEnforcer(max_tool_calls_total=2)
        assert enforcer.check_tool_call("web_search").triggered is False
        enforcer.record_tool_call("web_search")
        assert enforcer.check_tool_call("sql_query").triggered is False
        enforcer.record_tool_call("sql_query")
        # Third call should exceed
        verdict = enforcer.check_tool_call("web_search")
        assert verdict.triggered
        assert verdict.reason == "total_exceeded"

    def test_per_tool_limit(self):
        enforcer = ThresholdEnforcer(
            max_tool_calls_total=100,
            tool_call_limits={"web_search": 2},
        )
        enforcer.record_tool_call("sql_query")  # not limited
        enforcer.record_tool_call("web_search")
        enforcer.record_tool_call("web_search")
        verdict = enforcer.check_tool_call("web_search")
        assert verdict.triggered
        assert verdict.reason == "tool_exceeded"
        assert verdict.detail is not None
        assert verdict.detail["tool"] == "web_search"

    def test_timeout_detection(self):
        enforcer = ThresholdEnforcer(agent_timeout_seconds=-1.0)
        verdict = enforcer.check_timeout()
        assert verdict.triggered
        assert verdict.reason == "timeout"

    def test_no_timeout(self):
        enforcer = ThresholdEnforcer(agent_timeout_seconds=9999)
        verdict = enforcer.check_timeout()
        assert not verdict.triggered

    def test_properties(self):
        enforcer = ThresholdEnforcer()
        assert enforcer.total_count == 0
        enforcer.record_tool_call("web_search")
        assert enforcer.total_count == 1
        assert enforcer.elapsed_seconds >= 0
        assert enforcer.tool_counts == {"web_search": 1}


class TestGuardrailVerdicts:
    """Y1: GuardrailVerdict / StuckVerdict 类型创建。"""

    def test_guardrail_ok(self):
        v = GuardrailVerdict.ok()
        assert not v.triggered
        assert v.layer == 0
        assert v.reason == "ok"

    def test_guardrail_total_exceeded(self):
        v = GuardrailVerdict.total_exceeded(30, 35)
        assert v.triggered
        assert v.layer == 4
        assert v.reason == "total_exceeded"
        assert v.detail == {"limit": 30, "actual": 35}

    def test_guardrail_tool_exceeded(self):
        v = GuardrailVerdict.tool_exceeded("web_search", 5, 6)
        assert v.triggered
        assert v.reason == "tool_exceeded"
        assert v.detail == {"tool": "web_search", "limit": 5, "actual": 6}

    def test_guardrail_timeout(self):
        v = GuardrailVerdict.timeout(300.0)
        assert v.triggered
        assert v.layer == 4
        assert v.reason == "timeout"
        assert v.detail == {"timeout_seconds": 300.0}

    def test_guardrail_stuck(self):
        v = GuardrailVerdict.stuck("empty_tools", {"consecutive": 3})
        assert v.triggered
        assert v.layer == 3
        assert v.reason == "stuck_empty_tools"

    def test_stuck_ok(self):
        v = StuckVerdict.ok()
        assert not v.stuck
        assert v.reason == "ok"

    def test_stuck_empty_tools(self):
        v = StuckVerdict.empty_tools(3)
        assert v.stuck
        assert v.reason == "empty_tools"
        assert v.detail == {"consecutive": 3}

    def test_stuck_identical_calls(self):
        v = StuckVerdict.identical_calls("web_search", 3)
        assert v.stuck
        assert v.reason == "identical_calls"
        assert v.detail == {"tool": "web_search", "consecutive": 3}

    def test_stuck_output_loop(self):
        v = StuckVerdict.output_loop(0.95)
        assert v.stuck
        assert v.reason == "output_loop"
        assert v.detail == {"similarity": 0.95}

    def test_frozen_dataclass(self):
        v = GuardrailVerdict.ok()
        with pytest.raises(AttributeError):
            v.triggered = True  # type: ignore[misc]


class TestPrometheusGuardrailMetrics:
    """Y1: prometheus_text 包含 guardrail 指标。"""

    def setup_method(self) -> None:
        reset_agent_perf_metrics_for_tests()

    def test_guardrail_triggered_in_prometheus(self):
        m = get_agent_perf_metrics()
        m.record_guardrail_triggered(layer=4, reason="total_exceeded")
        m.record_guardrail_triggered(layer=3, reason="stuck_empty_tools")
        text = m.prometheus_text()
        assert "# HELP guardrail_triggered_total" in text
        assert "# TYPE guardrail_triggered_total counter" in text
        assert 'guardrail_triggered_total{layer="4",reason="total_exceeded"} 1' in text
        assert 'guardrail_triggered_total{layer="3",reason="stuck_empty_tools"} 1' in text

    def test_guardrail_stuck_in_prometheus(self):
        m = get_agent_perf_metrics()
        m.record_guardrail_stuck(reason="empty_tools")
        m.record_guardrail_stuck(reason="identical_calls")
        text = m.prometheus_text()
        assert "# HELP guardrail_stuck_total" in text
        assert "# TYPE guardrail_stuck_total counter" in text
        assert 'guardrail_stuck_total{reason="empty_tools"} 1' in text
        assert 'guardrail_stuck_total{reason="identical_calls"} 1' in text

    def test_guardrail_metrics_empty_when_no_data(self):
        m = get_agent_perf_metrics()
        text = m.prometheus_text()
        assert "# HELP guardrail_triggered_total" in text
        assert "# HELP guardrail_stuck_total" in text
        # metric lines themselves should not appear
        assert "guardrail_triggered_total{layer=" not in text
        assert "guardrail_stuck_total{reason=" not in text
