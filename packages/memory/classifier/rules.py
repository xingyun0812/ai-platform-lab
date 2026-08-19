from __future__ import annotations

from packages.memory.classifier.config import RulePatterns
from packages.memory.classifier.types import ClassResult


def rule_classify(content: str, patterns: RulePatterns | None = None) -> ClassResult | None:
    """Rule-based memory classifier.

    Returns ClassResult if confident, None if uncertain (delegate to LLM).
    """
    if patterns is None:
        patterns = RulePatterns()

    content = content.strip()
    content_lower = content.lower()

    # Check noise first (fast path)
    if content in patterns.noise_keywords or content in [
        w.lower() for w in patterns.noise_keywords
    ]:
        return ClassResult("noise", confidence=1.0, source="rule", reason="noise keyword matched")
    if patterns.noise_max_length > 0 and len(content) <= patterns.noise_max_length:
        return ClassResult("noise", confidence=0.9, source="rule", reason="content too short")

    # Preference indicators
    pref_hits = sum(1 for kw in patterns.preference_indicators if kw in content_lower)
    if pref_hits > 0:
        confidence = min(0.9, 0.5 + pref_hits * 0.2)
        if confidence >= patterns.rule_confidence_threshold:
            return ClassResult(
                "preference",
                confidence=confidence,
                source="rule",
                reason=f"preference indicators matched: {pref_hits}",
            )

    # Factual indicators
    factual_hits = sum(1 for kw in patterns.factual_indicators if kw in content_lower)
    if factual_hits > 0:
        confidence = min(0.9, 0.5 + factual_hits * 0.2)
        if confidence >= patterns.rule_confidence_threshold:
            return ClassResult(
                "factual",
                confidence=confidence,
                source="rule",
                reason=f"factual indicators matched: {factual_hits}",
            )

    # Uncertain
    return None
