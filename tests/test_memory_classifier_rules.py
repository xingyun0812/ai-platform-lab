from __future__ import annotations

import asyncio

from packages.memory.classifier import run_classifier
from packages.memory.classifier.config import RulePatterns
from packages.memory.classifier.rules import rule_classify
from packages.memory.config import MemoryGovernanceConfig


class TestRuleClassify:
    """X5a: Rule-based memory classifier — unit tests."""

    def test_noise_keyword_chinese(self):
        result = rule_classify("好的")
        assert result is not None
        assert result.class_label == "noise"
        assert result.confidence == 1.0
        assert result.source == "rule"

    def test_noise_keyword_english(self):
        result = rule_classify("hello")
        assert result is not None
        assert result.class_label == "noise"
        assert result.confidence == 1.0

    def test_noise_short_content(self):
        """Custom noise_max_length=3 -> short content is noise."""
        patterns = RulePatterns(noise_max_length=3)
        result = rule_classify("ab", patterns)
        assert result is not None
        assert result.class_label == "noise"
        assert result.confidence == 0.9
        assert "too short" in result.reason

    def test_short_content_not_noise_when_disabled(self):
        """Default noise_max_length=0 -> short content passes to preference/factual check."""
        result = rule_classify("平时喜欢简洁，偏好快速回复")
        assert result is not None
        assert result.class_label == "preference"

    def test_preference_indicator_chinese(self):
        """Long enough content with multiple preference indicators."""
        result = rule_classify("平时喜欢简洁风格，偏好快速回答方式呢")
        assert result is not None
        assert result.class_label == "preference"
        assert result.confidence >= 0.8
        assert result.source == "rule"

    def test_preference_indicator_english(self):
        """Multiple preference indicators to exceed the 0.8 threshold."""
        result = rule_classify("I prefer short answers and never want long explanations please")
        assert result is not None
        assert result.class_label == "preference"
        assert result.confidence >= 0.8

    def test_factual_indicator_version(self):
        """Long enough content with a factual indicator."""
        result = rule_classify("当前使用的 Python 版本为 3.11")
        assert result is not None
        assert result.class_label == "factual"
        assert result.confidence >= 0.8
        assert result.source == "rule"

    def test_factual_indicator_running_on(self):
        """Multiple factual indicators to exceed the 0.8 threshold."""
        result = rule_classify("这个服务运行在 8080 端口，使用的版本是 1.0")
        assert result is not None
        assert result.class_label == "factual"
        assert result.confidence >= 0.8

    def test_ambiguous_returns_none(self):
        """Content with no matching indicators should be uncertain."""
        result = rule_classify("今天天气不错，适合出去散步放松心情")
        assert result is None

    def test_custom_patterns_override(self):
        custom = RulePatterns(
            noise_keywords=["test_noise"],
            noise_max_length=3,
            preference_indicators=["custom_pref"],
            factual_indicators=["custom_fact"],
            rule_confidence_threshold=0.5,
        )
        # Custom noise keyword
        result = rule_classify("test_noise", patterns=custom)
        assert result is not None
        assert result.class_label == "noise"
        # Custom preference indicator with lowered threshold
        result = rule_classify("this has custom_pref in it", patterns=custom)
        assert result is not None
        assert result.class_label == "preference"
        # Custom factual indicator
        result = rule_classify("this has custom_fact in it", patterns=custom)
        assert result is not None
        assert result.class_label == "factual"

    def test_confidence_threshold_respected(self):
        """Weak match with high threshold returns None."""
        high_threshold = RulePatterns(
            noise_keywords=[],
            noise_max_length=0,
            preference_indicators=["like"],
            factual_indicators=[],
            rule_confidence_threshold=0.95,  # single match yields 0.7 < 0.95
        )
        result = rule_classify("I like this", patterns=high_threshold)
        assert result is None

    def test_empty_content_noise(self):
        """Empty content -> uncertain (None), quality_filter handles rejection."""
        result = rule_classify("")
        # With noise_max_length=0, empty content passes through
        # quality_filter is responsible for empty check
        assert result is None

    def test_multiple_indicators_higher_confidence(self):
        """Multiple preference indicators yield higher confidence."""
        result = rule_classify("喜欢简洁回答，不要长文，请直接说重点")
        assert result is not None
        assert result.class_label == "preference"
        # 3 hits: 0.5 + 3*0.2 = 1.1 -> min(0.9, 1.1) = 0.9
        assert result.confidence == 0.9


class TestRunClassifier:
    """X5a: run_classifier integration — config wiring."""

    def test_classifier_disabled(self):
        async def _run():
            config = MemoryGovernanceConfig(classifier_enabled=False)
            return await run_classifier("some content", config)

        result = asyncio.run(_run())
        assert result.class_label == "factual"
        assert result.source == "default"
        assert "disabled" in result.reason

    def test_classifier_fallback_when_llm_not_available(self):
        """When rule disabled and llm_call returns empty, use fallback."""

        async def llm_call(p: str) -> str:
            return ""

        async def _run():
            config = MemoryGovernanceConfig(
                classifier_enabled=True,
                classifier_rule_enabled=False,
                classifier_llm_model=None,
                classifier_llm_fallback_class="ephemeral",
            )
            return await run_classifier(
                "some content",
                config,
                llm_call=llm_call,
            )

        result = asyncio.run(_run())
        assert result.class_label == "ephemeral"
        assert result.source == "default"

    def test_rule_classifier_hit_shortcuts_llm(self):
        async def _run():
            config = MemoryGovernanceConfig(classifier_enabled=True)
            return await run_classifier("好的", config)

        result = asyncio.run(_run())
        assert result.class_label == "noise"
        assert result.source == "rule"
