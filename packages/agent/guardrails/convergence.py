"""收敛检测共享组件 — 从 self_refine/orchestrator.py 抽取。

支持 similarity / llm_judged / hybrid 三种策略。
Self-Refine 保留原有接口，内部调用此共享组件。
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("ai_platform.agent.guardrails.convergence")

_CONVERGENCE_JUDGE_SYSTEM = (
    "You are a convergence judge. Determine if the latest feedback identifies "
    "any new, actionable improvements beyond what was already addressed.\n"
    "Respond with exactly: CONVERGED or NOT_CONVERGED"
)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def check_convergence(
    *,
    strategy: str,
    current_output: str,
    previous_output: str | None = None,
    latest_feedback: str = "",
    threshold: float = 0.85,
    model: str | None = None,
    counter: list[int] | None = None,
) -> tuple[bool, str]:
    """判断是否收敛。

    Args:
        strategy: "similarity" | "llm_judged" | "hybrid"
        current_output: 当前的输出文本
        previous_output: 上一次的输出文本（用于 similarity 策略）
        latest_feedback: 最新的反馈/工具结果文本（用于 llm_judged）
        threshold: similarity 策略的余弦相似度阈值
        model: LLM judge 使用的模型（llm_judged/hybrid 策略）
        counter: 可选 LLM 调用计数器

    Returns:
        (converged, reason)
    """
    if strategy == "similarity":
        return await _check_similarity(current_output, previous_output, threshold)
    elif strategy == "llm_judged":
        return await _check_llm_judged(latest_feedback, model, temperature=0.3, counter=counter)
    elif strategy == "hybrid":
        sim_converged, sim_reason = await _check_similarity(
            current_output, previous_output, threshold
        )
        if sim_converged:
            return True, sim_reason
        return await _check_llm_judged(latest_feedback, model, temperature=0.3, counter=counter)
    else:
        logger.warning("unknown convergence strategy: %s", strategy)
        return False, "unknown_strategy"


async def _check_similarity(
    current: str,
    previous: str | None,
    threshold: float,
) -> tuple[bool, str]:
    """基于语义相似度判断收敛。"""
    if not previous:
        return False, "similarity_no_previous"

    try:
        from packages.rag.embeddings import embed_texts

        vectors = await embed_texts([current, previous])
        sim = cosine_similarity(vectors[0], vectors[1])
    except Exception:
        sim = 1.0 if current.strip() == previous.strip() else 0.0

    if sim >= threshold:
        return True, "similarity"
    return False, "similarity"


async def _check_llm_judged(
    latest_feedback: str,
    model: str | None,
    temperature: float = 0.3,
    *,
    counter: list[int] | None = None,
) -> tuple[bool, str]:
    """LLM 判断是否收敛。"""
    if not latest_feedback.strip():
        return True, "llm_judged"

    result = await _call_llm(
        system=_CONVERGENCE_JUDGE_SYSTEM,
        user=f"Latest feedback:\n{latest_feedback}",
        model=model,
        temperature=temperature,
        counter=counter,
    )

    if "CONVERGED" in result.strip().upper():
        return True, "llm_judged"
    return False, "llm_judged"


async def _call_llm(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
    *,
    counter: list[int] | None = None,
) -> str:
    """调用 LLM。"""
    from packages.platform import forward_with_model_router

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    route = await forward_with_model_router(payload)
    if counter is not None:
        counter[0] += 1
    if route.status != 200 or not route.body:
        return ""
    choices = route.body.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""
