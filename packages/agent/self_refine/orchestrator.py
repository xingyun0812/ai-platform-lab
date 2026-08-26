"""Self-Refine（Madaan et al., 2023）引擎。

收敛检测已抽取到 packages/agent/guardrails/convergence.py 共享组件。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.agent.guardrails.convergence import check_convergence
from packages.agent.reflection_gate import TRIGGER_TASK_FAILURE
from packages.agent.reflection_policy import ReflectionPolicy
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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（保留向后兼容）。"""
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def _call_llm(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.3,
    *,
    counter: list[int] | None = None,
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
    if counter is not None:
        counter[0] += 1
    if route.status != 200 or not route.body:
        return ""
    choices = route.body.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


async def generate(
    prompt: str,
    model: str | None,
    temperature: float,
    *,
    counter: list[int] | None = None,
) -> str:
    """初始输出生成。"""
    return await _call_llm(
        system="You are a helpful assistant. Produce the best possible output.",
        user=prompt,
        model=model,
        temperature=temperature,
        counter=counter,
    )


async def feedback(
    prompt: str,
    current_output: str,
    model: str | None,
    dimension: str | None,
    temperature: float,
    *,
    counter: list[int] | None = None,
) -> tuple[str, str | None]:
    """LLM 驱动的自我反馈。"""
    if dimension:
        system = _CORRECTNESS_FEEDBACK_SYSTEM
        user_msg = (
            f"Original request:\n{prompt}\n\n"
            f"Current output (focus on {dimension}):\n{current_output}"
        )
    else:
        system = _FREE_FEEDBACK_SYSTEM
        user_msg = f"Original request:\n{prompt}\n\nCurrent output:\n{current_output}"

    result = await _call_llm(
        system=system,
        user=user_msg,
        model=model,
        temperature=temperature,
        counter=counter,
    )
    return result, dimension


async def refine(
    prompt: str,
    current_output: str,
    feedback_text: str,
    model: str | None,
    temperature: float,
    *,
    counter: list[int] | None = None,
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
        counter=counter,
    )


async def convergence_check(
    strategy: str,
    threshold: float,
    current_output: str,
    previous_output: str,
    latest_feedback: str,
    prompt: str,
    model: str | None,
    temperature: float,
    *,
    counter: list[int] | None = None,
) -> tuple[bool, str]:
    """判断是否收敛（委托到共享组件，保持向后兼容）。"""
    return await check_convergence(
        strategy=strategy,
        current_output=current_output,
        previous_output=previous_output,
        latest_feedback=latest_feedback,
        threshold=threshold,
        model=model,
        counter=counter,
    )


def _resolve_reflection_mode(cfg: SelfRefineConfig) -> str:
    """经 ReflectionPolicy 解析反思深度（向后兼容：默认 legacy = 现状多轮迭代）。"""
    policy = ReflectionPolicy(default_depth=cfg.reflection_depth)
    return policy.resolve(cfg.reflection_depth)


async def _record_reflection_metric(
    *,
    depth: str,
    tokens: int,
    latency_ms: float,
    rounds: int,
) -> None:
    """记录一次 self_refine 反思使用到全局指标存储（失败静默，不阻塞主流程）。"""
    try:
        from packages.agent.perf_metrics import get_agent_perf_metrics

        get_agent_perf_metrics().record_reflection_use(
            reason=TRIGGER_TASK_FAILURE,
            depth=depth,
            tokens=tokens,
            latency_ms=latency_ms,
            rounds=rounds,
        )
    except Exception:
        # 指标记录失败不影响主流程。
        pass


async def run_self_refine(
    prompt: str,
    config: SelfRefineConfig | None = None,
    model: str | None = None,
) -> SelfRefineResult:
    """运行 Self-Refine 全流程。"""
    cfg = config or SelfRefineConfig()
    start = time.time()
    counter: list[int] = [0]
    trace: list[FeedbackRound] = []
    current_output = ""
    iteration = 0

    generator_model = cfg.generator_model or model
    feedback_model = cfg.feedback_model or generator_model
    # 反思深度经 ReflectionGate 策略解析（默认 legacy = 现状，向后兼容）。
    mode = _resolve_reflection_mode(cfg)

    # -- off / disabled：零反思 LLM，仅生成一次 -----------------------------
    if not cfg.enabled or mode == "off":
        current_output = await generate(
            prompt=prompt,
            model=generator_model,
            temperature=cfg.temperature,
            counter=counter,
        )
        elapsed = (time.time() - start) * 1000
        await _record_reflection_metric(
            depth="off", tokens=counter[0], latency_ms=elapsed, rounds=0
        )
        return SelfRefineResult(
            prompt=prompt,
            final_output=current_output,
            config=cfg,
            iterations_completed=0,
            converged=True,
            convergence_reason="disabled",
            trace=[],
            execution_time_ms=elapsed,
            total_llm_calls=counter[0],
            success=True,
        )

    # -- light：单轮即时校验（生成 + 一轮反馈/修正，不迭代收敛） ------------
    if mode == "light":
        current_output = await generate(
            prompt=prompt,
            model=generator_model,
            temperature=cfg.temperature,
            counter=counter,
        )
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
                total_llm_calls=counter[0],
                error="Generator returned empty output",
                success=False,
            )

        iteration = 1
        round_start = time.time()
        dimensions = cfg.feedback_dimensions
        dim = dimensions[0] if dimensions else None
        try:
            fb_text, _fb_dim = await feedback(
                prompt=prompt,
                current_output=current_output,
                model=feedback_model,
                dimension=dim,
                temperature=cfg.temperature,
                counter=counter,
            )
            new_output = await refine(
                prompt=prompt,
                current_output=current_output,
                feedback_text=fb_text,
                model=generator_model,
                temperature=cfg.temperature,
                counter=counter,
            )
            if new_output.strip():
                current_output = new_output
        except Exception as exc:
            logger.warning("light single-pass refine failed: %s", exc)
        round_elapsed = (time.time() - round_start) * 1000
        trace.append(
            FeedbackRound(
                iteration=iteration,
                feedback=fb_text if "fb_text" in dir() else "",
                feedback_dimension=dim,
                output_after_refine=current_output,
                elapsed_ms=round_elapsed,
            )
        )
        elapsed = (time.time() - start) * 1000
        await _record_reflection_metric(
            depth="light", tokens=counter[0], latency_ms=elapsed, rounds=1
        )
        return SelfRefineResult(
            prompt=prompt,
            final_output=current_output,
            config=cfg,
            iterations_completed=iteration,
            converged=True,
            convergence_reason="light_single_pass",
            trace=trace,
            execution_time_ms=elapsed,
            total_llm_calls=counter[0],
            success=True,
        )

    # -- legacy / full：多轮迭代 + 收敛判停（existing loop，向后兼容）--------
    try:
        current_output = await generate(
            prompt=prompt,
            model=generator_model,
            temperature=cfg.temperature,
            counter=counter,
        )

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
                total_llm_calls=counter[0],
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
                    total_llm_calls=counter[0],
                    success=True,
                )

            if counter[0] >= cfg.max_total_llm_calls:
                break

            iteration += 1
            round_start = time.time()
            new_output = current_output

            dimensions = cfg.feedback_dimensions
            dim = dimensions[(iteration - 1) % len(dimensions)] if dimensions else None

            fb_text: str = ""
            fb_dim: str | None = dim
            fb_error: str | None = None

            for attempt in range(2):
                try:
                    fb_text, fb_dim = await feedback(
                        prompt=prompt,
                        current_output=current_output,
                        model=feedback_model,
                        dimension=dim,
                        temperature=cfg.temperature,
                        counter=counter,
                    )
                    fb_error = None
                    break
                except Exception as exc:
                    fb_error = str(exc)
                    logger.warning("feedback attempt %d failed: %s", attempt + 1, exc)
                    if attempt == 0:
                        continue
                    fb_text = ""
                    fb_dim = dim

            is_no_improvement = "NO_IMPROVEMENT_NEEDED" in (fb_text or "").strip().upper()
            if is_no_improvement or not fb_text.strip():
                round_elapsed = (time.time() - round_start) * 1000
                trace.append(
                    FeedbackRound(
                        iteration=iteration,
                        feedback=fb_text,
                        feedback_dimension=fb_dim,
                        feedback_error=fb_error,
                        output_after_refine=current_output,
                        elapsed_ms=round_elapsed,
                    )
                )
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
                    total_llm_calls=counter[0],
                    success=True,
                )

            refine_error: str | None = None
            for attempt in range(2):
                try:
                    new_output = await refine(
                        prompt=prompt,
                        current_output=current_output,
                        feedback_text=fb_text,
                        model=generator_model,
                        temperature=cfg.temperature,
                        counter=counter,
                    )
                    refine_error = None
                    break
                except Exception as exc:
                    refine_error = str(exc)
                    logger.warning("refine attempt %d failed: %s", attempt + 1, exc)
                    if attempt == 0:
                        continue
                    new_output = current_output

            round_elapsed = (time.time() - round_start) * 1000
            trace.append(
                FeedbackRound(
                    iteration=iteration,
                    feedback=fb_text,
                    feedback_dimension=fb_dim,
                    feedback_error=fb_error,
                    refine_error=refine_error,
                    output_after_refine=new_output,
                    elapsed_ms=round_elapsed,
                )
            )

            if counter[0] >= cfg.max_total_llm_calls:
                current_output = new_output
                break

            converged, reason = await check_convergence(
                strategy=cfg.convergence_strategy,
                threshold=cfg.convergence_threshold,
                current_output=new_output,
                previous_output=current_output,
                latest_feedback=fb_text,
                model=feedback_model,
                counter=counter,
            )

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
                    total_llm_calls=counter[0],
                    success=True,
                )

        elapsed = (time.time() - start) * 1000
        reason = "max_calls" if counter[0] >= cfg.max_total_llm_calls else "max_iterations"
        await _record_reflection_metric(
            depth=mode, tokens=counter[0], latency_ms=elapsed, rounds=iteration
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
            total_llm_calls=counter[0],
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
            total_llm_calls=counter[0],
            error=str(exc),
            success=False,
        )
