from __future__ import annotations

import asyncio
import json as _json
import logging
import time as _time

from packages.memory.classifier import ClassResult
from packages.memory.config import MemoryGovernanceConfig

logger = logging.getLogger("ai_platform.memory.classifier.llm")

_CLASSIFIER_SYSTEM_PROMPT = """你是一个记忆分类助手。判断以下记忆内容属于哪个类别。

类别说明：
- preference: 用户的偏好、习惯、风格要求（如"我喜欢简洁回答"）
- factual: 事实性信息、知识、决策记录（如"服务运行在 Python 3.11"）
- ephemeral: 临时状态、上下文、会话中的过程信息（如"我们在调试 Issue #221"）
- noise: 无信息量的寒暄、重复、确认（如"好的"、"明白了"）

输出 JSON：{"class": "...", "confidence": 0.0~1.0, "reason": "..."}
只输出 JSON，不要额外解释。"""


def _default_user_prompt(content: str) -> str:
    return f"记忆内容：{content}\n\n分类结果："


async def llm_classify(
    content: str,
    config: MemoryGovernanceConfig,
    *,
    llm_call=None,
) -> ClassResult:
    """LLM-based memory classifier."""
    t0 = _time.perf_counter()

    prompt = _default_user_prompt(content)

    if llm_call is not None:
        result = await llm_call(prompt)
    else:
        result = await _call_classifier_llm(prompt, config)

    latency_ms = (_time.perf_counter() - t0) * 1000

    _record_latency(config, latency_ms, source="llm" if llm_call is None else "mock")

    if not result or not result.strip():
        logger.warning("LLM classifier returned empty response, defaulting to ephemeral")
        return ClassResult(
            config.classifier_llm_fallback_class,
            confidence=0.5,
            source="default",
            reason="empty LLM response",
        )

    return _parse_llm_response(result, config)


async def _call_classifier_llm(prompt: str, config: MemoryGovernanceConfig) -> str:
    """Call LLM with timeout."""
    from packages.platform import forward_with_model_router, get_settings

    settings = get_settings()
    model = config.classifier_llm_model or settings.default_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        routed = await asyncio.wait_for(
            forward_with_model_router(payload, requested_model=model),
            timeout=config.classifier_timeout_ms / 1000.0,
        )
        if routed.error or routed.body is None:
            logger.warning("LLM classifier error: %s", routed.error)
            return ""
        choices = routed.body.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        return content if isinstance(content, str) else ""
    except TimeoutError:
        logger.warning("LLM classifier timed out after %dms", config.classifier_timeout_ms)
        return ""
    except Exception as e:
        logger.warning("LLM classifier exception: %s", e)
        return ""


def _parse_llm_response(content: str, config: MemoryGovernanceConfig) -> ClassResult:
    """Parse LLM JSON response."""
    text = content.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        parsed = _json.loads(text)
        class_label = str(parsed.get("class", config.classifier_llm_fallback_class))
        confidence = float(parsed.get("confidence", 0.5))
        reason = str(parsed.get("reason", ""))

        # Validate class_label — include noise which the rule classifier can return
        valid_classes = {"preference", "factual", "ephemeral", "noise"}
        if class_label not in valid_classes:
            logger.warning(
                "LLM returned invalid class %s, defaulting to %s",
                class_label,
                config.classifier_llm_fallback_class,
            )
            class_label = config.classifier_llm_fallback_class
            confidence = 0.5

        return ClassResult(class_label, confidence=confidence, source="llm", reason=reason)
    except (_json.JSONDecodeError, ValueError, TypeError):
        logger.warning("LLM classifier response not valid JSON")
        return ClassResult(
            config.classifier_llm_fallback_class,
            confidence=0.5,
            source="default",
            reason="parse error",
        )


def _record_latency(config: MemoryGovernanceConfig, latency_ms: float, source: str) -> None:
    """Record latency to metrics."""
    try:
        from packages.memory.metrics import get_memory_metrics

        get_memory_metrics().record_classifier_latency(source=source, latency_ms=latency_ms)
    except Exception:
        pass


__all__ = [
    "llm_classify",
]
