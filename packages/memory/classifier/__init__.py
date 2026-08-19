from __future__ import annotations

from typing import Any

from packages.memory.classifier.config import RulePatterns
from packages.memory.classifier.rules import rule_classify
from packages.memory.classifier.types import ClassResult


def _get_classifier_config(
    config: Any,
) -> tuple[bool, bool, object | None, str]:
    """Extract classifier config from MemoryGovernanceConfig-like object."""
    enabled = getattr(config, "classifier_enabled", True)
    rule_enabled = getattr(config, "classifier_rule_enabled", True)
    llm_model = getattr(config, "classifier_llm_model", None)
    fallback = getattr(config, "classifier_llm_fallback_class", "ephemeral")
    return enabled, rule_enabled, llm_model, fallback


async def run_classifier(
    content: str,
    config: Any,
    *,
    llm_call=None,
) -> ClassResult:
    """Run dual-track classifier: rule first, then LLM if uncertain.

    Stage 1: Rule classifier (fast, synchronous)
    Stage 2: LLM classifier (async, used when rule returns None)
    """
    enabled, rule_enabled, llm_model, fallback = _get_classifier_config(config)

    if not enabled:
        return ClassResult(
            "factual", confidence=0.5, source="default", reason="classifier disabled"
        )

    # Stage 1: Rule classifier
    if rule_enabled:
        patterns = RulePatterns()
        result = rule_classify(content, patterns)
        if result is not None:
            return result

    # Stage 2: LLM classifier
    from packages.memory.classifier.llm import llm_classify

    return await llm_classify(content, config, llm_call=llm_call)
