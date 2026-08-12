from __future__ import annotations

import logging
import time
from typing import Any

from packages.agent.self_refine.config import SelfRefineConfig
from packages.agent.self_refine.models import FeedbackRound, SelfRefineResult

logger = logging.getLogger("ai_platform.agent.self_refine")

_CORRECTNESS_FEEDBACK_SYSTEM = (
    "You are a quality reviewer. Analyze the following output and identify "
    "specific issues, errors, or areas for improvement. "
    "Focus on: correctness, clarity, completeness, consistency, and actionability.\n"
    "If the output is already optimal, respond with exactly: NO_IMPROVEMENT_NEEDED"
)

_FREE_FEEDBACK_SYSTEM = (
    "You are a quality reviewer. Analyze the following output and identify "
    "specific issues, errors, or areas for improvement.\n"
    "If the output is already optimal, respond with exactly: NO_IMPROVEMENT_NEEDED"
)

_REFINE_SYSTEM = (
    "You are an output refiner. Revise the output based on the feedback provided. "
    "Keep what works, fix what doesn't. Return only the revised output."
)

_CONVERGENCE_JUDGE_SYSTEM = (
    "You are a convergence judge. Determine if the latest feedback identifies "
    "any new, actionable improvements beyond what was already addressed.\n"
    "Respond with exactly: CONVERGED or NOT_CONVERGED"
)


async def _call_llm(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
) -> str:
    """调用 LLM，返回文本响应。"""
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
    if route.status != 200 or not route.body:
        return ""
    choices = route.body.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


async def generate(
    prompt: str,
    context: str | None,
    model: str | None,
    temperature: float,
) -> str:
    """初始输出生成。"""
    user_msg = prompt
    if context:
        user_msg = f"Context:\n{context}\n\nTask:\n{prompt}"

    return await _call_llm(
        system="You are a helpful assistant. Produce the best possible output.",
        user=user_msg,
        model=model,
        temperature=temperature,
    )


async def feedback(
    prompt: str,
    current_output: str,
    model: str | None,
    dimension: str | None,
    temperature: float,
) -> tuple[str, str | None]:
    """LLM 驱动的自我反馈。

    Returns:
        (feedback_text, dimension_or_None)
    """
    if dimension:
        system = _CORRECTNESS_FEEDBACK_SYSTEM
        user_msg = (
            f"Original request:\n{prompt}\n\n"
            f"Current output (focus on {dimension}):\n{current_output}"
        )
    else:
        system = _FREE_FEEDBACK_SYSTEM
        user_msg = (
            f"Original request:\n{prompt}\n\n"
            f"Current output:\n{current_output}"
        )

    result = await _call_llm(
        system=system,
        user=user_msg,
        model=model,
        temperature=temperature,
    )
    return result, dimension


async def refine(
    prompt: str,
    current_output: str,
    feedback_text: str,
    model: str | None,
    temperature: float,
) -> str:
    """基于反馈修正输出。"""
    user_msg = (
        f"Original request:\n{prompt}\n\n"
        f"Current output:\n{current_output}\n\n"
        f"Feedback to address:\n{feedback_text}\n\n"
        "Return only the revised output."
    )
    return await _call_llm(
        system=_REFINE_SYSTEM,
        user=user_msg,
        model=model,
        temperature=temperature,
    )


async def convergence_check(
    strategy: str,
    threshold: float,
    current_output: str,
    previous_output: str,
    latest_feedback: str,
    prompt: str,  # kept for future LLM-judge context
    model: str | None,
    temperature: float,
) -> tuple[bool, str]:
    """判断是否收敛。

    Returns:
        (converged, reason)
    """
    if strategy == "similarity":
        return await _check_similarity(current_output, previous_output, threshold)
    elif strategy == "llm_judged":
        return await _check_llm_judged(latest_feedback, model, temperature)
    elif strategy == "hybrid":
        # sequential AND: similarity quick check first
        sim_converged, sim_reason = await _check_similarity(
            current_output, previous_output, threshold
        )
        if sim_converged:
            return True, sim_reason
        # LLM judge to confirm
        return await _check_llm_judged(latest_feedback, model, temperature)
    else:
        return False, "unknown_strategy"


async def _check_similarity(
    current: str,
    previous: str,
    threshold: float,
) -> tuple[bool, str]:
    """基于语义相似度判断收敛。

    如果相似度 >= threshold 则认为收敛。
    当 embedding 服务不可用时，回退到字符串精确匹配。
    """
    if not previous:
        return False, "similarity_no_previous"

    try:
        from packages.agent.tools.embedding import compute_similarity

        sim = await compute_similarity(current, previous)
    except (ImportError, Exception):
        # Fallback: exact match
        sim = 1.0 if current.strip() == previous.strip() else 0.0

    if sim >= threshold:
        return True, "similarity"
    return False, "similarity"


async def _check_llm_judged(
    latest_feedback: str,
    model: str | None,
    temperature: float,
) -> tuple[bool, str]:
    """LLM 判断是否收敛。"""
    if not latest_feedback.strip():
        return True, "llm_judged"

    result = await _call_llm(
        system=_CONVERGENCE_JUDGE_SYSTEM,
        user=f"Latest feedback:\n{latest_feedback}",
        model=model,
        temperature=temperature,
    )

    if "CONVERGED" in result.strip().upper():
        return True, "llm_judged"
    return False, "llm_judged"


async def run_self_refine(
    prompt: str,
    context: str | None = None,
    config: SelfRefineConfig | None = None,
    model: str | None = None,
) -> SelfRefineResult:
    """运行 Self-Refine 全流程。

    与 self_evolve.py（跨 session 经验积累 + 策略补丁）不同，
    Self-Refine 是单次请求内的迭代修正，不涉及持久化。

    Args:
        prompt: 用户 prompt。
        context: 可选背景信息（memory/RAG 摘要等）。
        config: Self-Refine 配置。缺省使用默认值。
        model: 模型名。缺省使用 generator_model 或 settings 默认。

    Returns:
        SelfRefineResult 包含最终输出、迭代轨迹、统计信息。
    """
    cfg = config or SelfRefineConfig()
    start = time.time()
    llm_call_count = 0
    trace: list[FeedbackRound] = []
    current_output = ""
    iteration = 0

    generator_model = cfg.generator_model or model
    feedback_model = cfg.feedback_model or generator_model

    try:
        # W1: 初始生成
        current_output = await generate(
            prompt=prompt,
            context=context,
            model=generator_model,
            temperature=cfg.temperature,
        )
        llm_call_count += 1

        if not current_output.strip():
            elapsed = (time.time() - start) * 1000
            return SelfRefineResult(
                prompt=prompt,
                final_output="",
                config=cfg,
                iterations_completed=0,
                converged=True,
                convergence_reason="empty_generation",
                trace=[],
                execution_time_ms=elapsed,
                total_llm_calls=1,
                error="Generator returned empty output",
                success=False,
            )

        iteration = 0

        while iteration < cfg.max_iterations:
            if time.time() - start > cfg.timeout_seconds:
                elapsed = (time.time() - start) * 1000
                return SelfRefineResult(
                    prompt=prompt,
                    final_output=current_output,
                    config=cfg,
                    iterations_completed=iteration,
                    converged=False,
                    convergence_reason="timeout",
                    trace=trace,
                    execution_time_ms=elapsed,
                    total_llm_calls=llm_call_count,
                    success=True,
                )

            if llm_call_count >= cfg.max_total_llm_calls:
                break

            iteration += 1
            round_start = time.time()
            new_output = current_output  # default: keep current if refine fails

            # 确定本轮反馈维度
            dimensions = cfg.feedback_dimensions
            dim = (
                dimensions[(iteration - 1) % len(dimensions)]
                if dimensions else None
            )

            # W2: 自我反馈（带重试）
            fb_text: str = ""
            fb_dim: str | None = dim
            fb_error: str | None = None

            for attempt in range(2):  # retry-1
                try:
                    fb_text, fb_dim = await feedback(
                        prompt=prompt,
                        current_output=current_output,
                        model=feedback_model,
                        dimension=dim,
                        temperature=cfg.temperature,
                    )
                    llm_call_count += 1
                    fb_error = None
                    break
                except Exception as exc:
                    fb_error = str(exc)
                    logger.warning(
                        "feedback attempt %d failed: %s", attempt + 1, exc
                    )
                    if attempt == 0:
                        continue
                    # Degrade: empty feedback, don't break the request
                    fb_text = ""
                    fb_dim = dim

            # 空反馈 = 已最优，直接收敛
            is_no_improvement = (
                "NO_IMPROVEMENT_NEEDED" in (fb_text or "").strip().upper()
            )
            if is_no_improvement or not fb_text.strip():
                round_elapsed = (time.time() - round_start) * 1000
                trace.append(FeedbackRound(
                    iteration=iteration,
                    feedback=fb_text,
                    feedback_dimension=fb_dim,
                    feedback_error=fb_error,
                    output_after_refine=current_output,
                    elapsed_ms=round_elapsed,
                ))
                elapsed = (time.time() - start) * 1000
                return SelfRefineResult(
                    prompt=prompt,
                    final_output=current_output,
                    config=cfg,
                    iterations_completed=iteration,
                    converged=True,
                    convergence_reason="no_improvement_needed",
                    trace=trace,
                    execution_time_ms=elapsed,
                    total_llm_calls=llm_call_count,
                    success=True,
                )

            # W3: 自我修正（带重试）
            refine_error: str | None = None
            for attempt in range(2):
                try:
                    new_output = await refine(
                        prompt=prompt,
                        current_output=current_output,
                        feedback_text=fb_text,
                        model=generator_model,
                        temperature=cfg.temperature,
                    )
                    llm_call_count += 1
                    refine_error = None
                    break
                except Exception as exc:
                    refine_error = str(exc)
                    logger.warning(
                        "refine attempt %d failed: %s", attempt + 1, exc
                    )
                    if attempt == 0:
                        continue
                    # Keep previous output
                    new_output = current_output

            round_elapsed = (time.time() - round_start) * 1000
            trace.append(FeedbackRound(
                iteration=iteration,
                feedback=fb_text,
                feedback_dimension=fb_dim,
                feedback_error=fb_error,
                refine_error=refine_error,
                output_after_refine=new_output,
                elapsed_ms=round_elapsed,
            ))

            # W4: 收敛检查
            if llm_call_count >= cfg.max_total_llm_calls:
                current_output = new_output
                break

            converged, reason = await convergence_check(
                strategy=cfg.convergence_strategy,
                threshold=cfg.convergence_threshold,
                current_output=new_output,
                previous_output=current_output,
                latest_feedback=fb_text,
                prompt=prompt,
                model=feedback_model,
                temperature=cfg.temperature,
            )
            # convergence_check may call LLM for llm_judged/hybrid
            if reason == "llm_judged":
                llm_call_count += 1

            current_output = new_output

            if converged:
                elapsed = (time.time() - start) * 1000
                return SelfRefineResult(
                    prompt=prompt,
                    final_output=current_output,
                    config=cfg,
                    iterations_completed=iteration,
                    converged=True,
                    convergence_reason=reason,
                    trace=trace,
                    execution_time_ms=elapsed,
                    total_llm_calls=llm_call_count,
                    success=True,
                )

        # Exited loop: max_iterations or max calls
        elapsed = (time.time() - start) * 1000
        reason = (
            "max_calls"
            if llm_call_count >= cfg.max_total_llm_calls
            else "max_iterations"
        )
        return SelfRefineResult(
            prompt=prompt,
            final_output=current_output,
            config=cfg,
            iterations_completed=iteration,
            converged=False,
            convergence_reason=reason,
            trace=trace,
            execution_time_ms=elapsed,
            total_llm_calls=llm_call_count,
            success=True,
        )

    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.exception("self_refine failed")
        return SelfRefineResult(
            prompt=prompt,
            final_output=current_output if "current_output" in dir() else "",
            config=cfg,
            iterations_completed=iteration if "iteration" in dir() else 0,
            converged=False,
            convergence_reason="error",
            trace=trace,
            execution_time_ms=elapsed,
            total_llm_calls=llm_call_count,
            error=str(exc),
            success=False,
        )
