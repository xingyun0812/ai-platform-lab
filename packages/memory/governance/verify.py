from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from packages.memory.config import MemoryGovernanceConfig
from packages.memory.store import MemoryRecord

logger = logging.getLogger("ai_platform.memory.verify")


@dataclass
class Verdict:
    """召回校验结果。"""

    relevant: bool
    confidence: float  # 0.0 ~ 1.0
    reason: str = ""


@dataclass
class VerifyResult:
    """验证输出。"""

    memory_id: str
    original_rank: int
    original_score: float
    demoted_score: float
    verdict: Verdict
    demoted: bool
    latency_ms: float = 0.0


def _default_verify_prompt(query: str, content: str) -> str:
    return f"""判断以下记忆是否与当前查询相关。

查询：{query}

记忆内容：{content}

请输出 JSON：{{"relevant": true/false, "confidence": 0.0~1.0, "reason": "简要原因"}}
只输出 JSON 本身，不要额外解释。"""


async def verify_relevance(
    query: str,
    memory: MemoryRecord,
    config: MemoryGovernanceConfig,
    *,
    llm_call: Callable[..., Any] | None = None,
) -> Verdict:
    """校验单条记忆是否与当前查询相关。

    Args:
        query: 搜索查询
        memory: 待校验的记忆
        config: 治理配置
        llm_call: 可选的 LLM 调用函数，用于测试注入 mock

    Returns:
        Verdict 对象。LLM 不可用或解析失败时默认放行（degrade gracefully）。
    """
    if not config.verify_enabled:
        return Verdict(relevant=True, confidence=1.0, reason="verify disabled")

    prompt = _default_verify_prompt(query, memory.content)

    if llm_call is not None:
        result = await llm_call(prompt)
    else:
        result = await _call_verify_llm(prompt, config)

    return _parse_llm_response(result) if isinstance(result, str) else result


async def _call_verify_llm(prompt: str, config: MemoryGovernanceConfig) -> str:
    """调用 LLM 进行校验。"""
    from packages.platform import forward_with_model_router, get_settings

    settings = get_settings()
    model = config.verify_model or settings.default_model
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是记忆相关性校验助手，只输出 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        routed = await forward_with_model_router(payload, requested_model=model)
        if routed.error or routed.body is None:
            logger.warning("verify LLM call error: %s", routed.error)
            return ""
        choices = routed.body.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        return content if isinstance(content, str) else ""
    except Exception as e:
        logger.warning("verify LLM call exception: %s", e)
        return ""


def _parse_llm_response(content: str) -> Verdict:
    """解析 LLM 返回的 JSON 字符串。"""
    if not content or not content.strip():
        return Verdict(relevant=True, confidence=0.5, reason="empty response, default pass")

    text = content.strip()
    # 尝试从 markdown code block 提取
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        parsed = _json.loads(text)
        relevant = bool(parsed.get("relevant", True))
        confidence = float(parsed.get("confidence", 0.5))
        reason = str(parsed.get("reason", ""))
        return Verdict(relevant=relevant, confidence=confidence, reason=reason)
    except (_json.JSONDecodeError, ValueError, TypeError):
        logger.warning("verify LLM response not valid JSON, defaulting to relevant")
        return Verdict(relevant=True, confidence=0.5, reason="parse error, default pass")


async def verify_top_k(
    query: str,
    results: list[MemoryRecord],
    config: MemoryGovernanceConfig,
    *,
    llm_call: Callable[..., Any] | None = None,
) -> list[VerifyResult]:
    """验证 top-k 搜索结果，只校验第 1 条。

    根据 PRD 决策 #4，只验证 top-1。如果 demote 则将其推到阈值之下。

    Returns:
        每条验证结果列表（长度 ≤ 1）。
    """
    if not config.verify_enabled or not results:
        return []

    import time as _time

    t0 = _time.perf_counter()

    top = results[0]
    verdict = await verify_relevance(query, top, config, llm_call=llm_call)
    latency_ms = (_time.perf_counter() - t0) * 1000

    demoted = not verdict.relevant and verdict.confidence >= config.verify_confidence_threshold

    demoted_score = config.verify_demote_threshold if demoted else 0.5

    return [
        VerifyResult(
            memory_id=top.memory_id,
            original_rank=0,
            original_score=1.0,
            demoted_score=demoted_score,
            verdict=verdict,
            demoted=demoted,
            latency_ms=latency_ms,
        )
    ]


def verify_top_k_sync(
    query: str,
    results: list[MemoryRecord],
    config: MemoryGovernanceConfig,
    *,
    llm_call: Callable[..., Any] | None = None,
) -> list[VerifyResult]:
    """同步包装器，用于从同步 search() 方法调用。

    若 llm_call 为 None（真实 LLM 路径），使用 asyncio.run() 运行异步调用。
    若 llm_call 提供且为同步函数（测试 mock），直接调用。
    """
    if not config.verify_enabled or not results:
        return []

    import time as _time

    t0 = _time.perf_counter()

    top = results[0]

    if llm_call is not None and not _is_async(llm_call):
        # Sync mock path — llm_call is a sync function that returns Verdict directly
        # Signature: llm_call(query, memory, config) -> Verdict
        verdict = llm_call(query, top, config)
    elif llm_call is not None:
        # Async mock path
        verdict = _run_async(verify_relevance(query, top, config, llm_call=llm_call))
    else:
        # Real LLM path
        verdict = _run_async(verify_relevance(query, top, config))

    latency_ms = (_time.perf_counter() - t0) * 1000

    if not isinstance(verdict, Verdict):
        verdict = Verdict(
            relevant=True, confidence=0.5, reason="invalid verdict type, default pass"
        )

    demoted = not verdict.relevant and verdict.confidence >= config.verify_confidence_threshold

    demoted_score = config.verify_demote_threshold if demoted else 0.5

    return [
        VerifyResult(
            memory_id=top.memory_id,
            original_rank=0,
            original_score=1.0,
            demoted_score=demoted_score,
            verdict=verdict,
            demoted=demoted,
            latency_ms=latency_ms,
        )
    ]


def _run_async(coro) -> Any:
    """Run a coroutine synchronously, handling nested event loops gracefully.

    When called from within a running event loop (e.g. tests via TestClient),
    spawns a new thread with its own event loop.
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
        # Already in an event loop — run in a new thread
        result: list[Any] = []
        exception: list[BaseException | None] = [None]

        def _run():
            try:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                r = new_loop.run_until_complete(coro)
                new_loop.close()
                result.append(r)
            except BaseException as e:
                exception[0] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()
        if exception[0]:
            raise exception[0]  # type: ignore[arg-type]
        return result[0]
    except RuntimeError:
        # No running loop
        return asyncio.run(coro)


def _is_async(fn: Callable[..., Any]) -> bool:
    import asyncio

    return asyncio.iscoroutinefunction(fn)
