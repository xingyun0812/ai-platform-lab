#!/usr/bin/env python3
"""Phase W: Self-Refine 单元测试。"""

from __future__ import annotations

import pytest

from packages.agent.self_refine import FeedbackRound, SelfRefineConfig, SelfRefineResult


class TestSelfRefineConfig:
    """W1: SelfRefineConfig 参数校验。"""

    def test_default_config(self):
        cfg = SelfRefineConfig()
        assert cfg.max_iterations == 5
        assert cfg.convergence_strategy == "hybrid"
        assert cfg.max_total_llm_calls == 15
        assert len(cfg.feedback_dimensions) == 5

    def test_max_iterations_validation(self):
        with pytest.raises(ValueError):
            SelfRefineConfig(max_iterations=0)
        with pytest.raises(ValueError):
            SelfRefineConfig(max_iterations=11)
        SelfRefineConfig(max_iterations=10)  # ok
        SelfRefineConfig(max_iterations=1)  # ok

    def test_max_total_llm_calls_validation(self):
        with pytest.raises(ValueError):
            SelfRefineConfig(max_total_llm_calls=0)
        with pytest.raises(ValueError):
            SelfRefineConfig(max_total_llm_calls=31)
        SelfRefineConfig(max_total_llm_calls=30)
        SelfRefineConfig(max_total_llm_calls=1)

    def test_convergence_strategy_validation(self):
        with pytest.raises(ValueError):
            SelfRefineConfig(convergence_strategy="invalid")
        for s in ("llm_judged", "similarity", "hybrid"):
            SelfRefineConfig(convergence_strategy=s)

    def test_convergence_threshold_validation(self):
        with pytest.raises(ValueError):
            SelfRefineConfig(convergence_threshold=-0.1)
        with pytest.raises(ValueError):
            SelfRefineConfig(convergence_threshold=1.1)

    def test_to_dict(self):
        cfg = SelfRefineConfig(max_iterations=3)
        d = cfg.to_dict()
        assert d["max_iterations"] == 3
        assert d["convergence_strategy"] == "hybrid"
        assert isinstance(d["feedback_dimensions"], list)
        assert len(d["feedback_dimensions"]) == 5

    def test_model_separation(self):
        cfg = SelfRefineConfig(generator_model="gpt-4", feedback_model="gpt-3.5")
        assert cfg.generator_model == "gpt-4"
        assert cfg.feedback_model == "gpt-3.5"

    def test_feedback_dimensions_custom(self):
        cfg = SelfRefineConfig(feedback_dimensions=("correctness", "clarity"))
        assert len(cfg.feedback_dimensions) == 2

    def test_enabled_false(self):
        cfg = SelfRefineConfig(enabled=False)
        assert not cfg.enabled

    def test_reflection_depth_default_is_legacy(self):
        """#256 向后兼容：未声明 reflection_depth 时默认 legacy（现状多轮迭代）。"""
        cfg = SelfRefineConfig()
        assert cfg.reflection_depth == "legacy"

    def test_reflection_depth_validation(self):
        for depth in ("full", "light", "off", "legacy"):
            SelfRefineConfig(reflection_depth=depth)
        with pytest.raises(ValueError):
            SelfRefineConfig(reflection_depth="super")

    def test_reflection_cost_fields(self):
        cfg = SelfRefineConfig(
            reflection_depth="full",
            small_model="cheap-model",
            confidence_gate_enabled=True,
            confidence_threshold=0.7,
            max_total_latency_s=30.0,
        )
        assert cfg.small_model == "cheap-model"
        assert cfg.confidence_gate_enabled is True
        assert cfg.confidence_threshold == 0.7
        assert cfg.max_total_latency_s == 30.0

    def test_reflection_to_dict_includes_depth(self):
        cfg = SelfRefineConfig(reflection_depth="light", max_total_latency_s=15.0)
        d = cfg.to_dict()
        assert d["reflection_depth"] == "light"
        assert d["max_total_latency_s"] == 15.0
        assert "small_model" in d


class TestFeedbackRound:
    """W1: FeedbackRound 数据结构。"""

    def test_defaults(self):
        fb = FeedbackRound(iteration=1, feedback="good", feedback_dimension="correctness")
        assert fb.iteration == 1
        assert fb.feedback == "good"
        assert fb.feedback_dimension == "correctness"
        assert fb.feedback_error is None
        assert fb.refine_error is None

    def test_structure(self):
        fb = FeedbackRound(
            iteration=2,
            feedback="needs work",
            feedback_dimension=None,
            feedback_error="api error",
            refine_error="timeout",
            output_after_refine="v2",
            elapsed_ms=100.0,
        )
        assert fb.feedback_dimension is None
        assert fb.feedback_error == "api error"

    def test_to_dict(self):
        fb = FeedbackRound(iteration=1, feedback="fix it", feedback_dimension="clarity")
        d = fb.to_dict()
        assert d["iteration"] == 1
        assert d["feedback_dimension"] == "clarity"


class TestSelfRefineResult:
    """W1: SelfRefineResult 数据结构。"""

    def test_default_converged_false(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(prompt="test", final_output="", config=cfg)
        assert not r.converged
        assert r.success
        assert r.iterations_completed == 0

    def test_converged(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(
            prompt="test",
            final_output="answer",
            config=cfg,
            iterations_completed=3,
            converged=True,
            convergence_reason="llm_judged",
            total_llm_calls=7,
        )
        assert r.converged
        assert r.convergence_reason == "llm_judged"
        assert r.total_llm_calls == 7

    def test_no_improvement_needed(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(
            prompt="test",
            final_output="optimal",
            config=cfg,
            converged=True,
            convergence_reason="no_improvement_needed",
        )
        assert r.convergence_reason == "no_improvement_needed"

    def test_error_state(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(
            prompt="test",
            final_output="",
            config=cfg,
            success=False,
            error="LLM call failed",
        )
        assert not r.success
        assert r.error == "LLM call failed"

    def test_trace(self):
        cfg = SelfRefineConfig()
        fb = FeedbackRound(iteration=1, feedback="fix", feedback_dimension="correctness")
        r = SelfRefineResult(
            prompt="test",
            final_output="v3",
            config=cfg,
            iterations_completed=1,
            trace=[fb],
        )
        assert len(r.trace) == 1
        assert r.trace[0].feedback == "fix"

    def test_to_dict_matches_fields(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(prompt="p", final_output="o", config=cfg, total_llm_calls=5)
        d = r.to_dict()
        assert d["total_llm_calls"] == 5
        assert d["config"]["max_iterations"] == 5

    def test_max_calls_reason(self):
        cfg = SelfRefineConfig()
        r = SelfRefineResult(
            prompt="p",
            final_output="o",
            config=cfg,
            converged=False,
            convergence_reason="max_calls",
        )
        assert r.convergence_reason == "max_calls"

    def test_max_iterations_boundary(self):
        cfg = SelfRefineConfig(max_iterations=10)
        r = SelfRefineResult(
            prompt="p",
            final_output="o",
            config=cfg,
            iterations_completed=10,
            converged=False,
            convergence_reason="max_iterations",
        )
        assert r.iterations_completed == 10

    def test_long_prompt_trace(self):
        cfg = SelfRefineConfig()
        trace = [FeedbackRound(i, f"fb{i}", "correctness", elapsed_ms=50.0) for i in range(1, 18)]
        r = SelfRefineResult(
            prompt="x" * 10000,
            final_output="o",
            config=cfg,
            iterations_completed=17,
            trace=trace,
            execution_time_ms=1000.0,
        )
        assert len(r.trace) == 17
        assert r.execution_time_ms == 1000.0

    def test_hybrid_similarity_skip(self):
        """hybrid 模式：similarity >= threshold 应跳过 LLM judge（cost saving）。"""
        cfg = SelfRefineConfig(convergence_strategy="hybrid", convergence_threshold=0.85)
        r = SelfRefineResult(
            prompt="p",
            final_output="o",
            config=cfg,
            converged=True,
            convergence_reason="similarity",
            total_llm_calls=5,
            iterations_completed=1,
        )
        assert r.convergence_reason == "similarity"
        assert r.converged
        # 如果 convergence_reason == "similarity"，说明 LLM judge 被跳过了
        # 实际 LLM 调用数应少于同场景 hybrid+llm_judged 路径


class TestConvergenceReasons:
    """收敛原因枚举覆盖。"""

    def test_all_convergence_reasons(self):
        reasons = [
            "llm_judged",
            "similarity",
            "max_iterations",
            "max_calls",
            "timeout",
            "error",
            "no_improvement_needed",
        ]
        cfg = SelfRefineConfig()
        for reason in reasons:
            r = SelfRefineResult(
                prompt="p",
                final_output="o",
                config=cfg,
                converged=reason in ("llm_judged", "similarity", "no_improvement_needed"),
                convergence_reason=reason,
            )
            assert r.convergence_reason == reason
