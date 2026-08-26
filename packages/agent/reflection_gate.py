"""统一反思网关（ReflectionGate）— 全项目反思成本治理单一前置入口。

对 **所有会调用 LLM 的反思/纠错链路**（self_refine 迭代、self_evolve 反思→策略 patch、
运行期即时校验）做集中决策：放行（pass）/ 降级（check）/ 拦截（skip/off）。

核心语义（ADR-0010）：\n
    off      ->  zero-LLM 直通，返回 pass 且不消耗任何 LLM。\n
    light    ->  同步 small-model one-pass 即时校验 + 置信度闸门；\n
                  低置信度升级大模型复核，复核失败/超时 fail-open。\n
    full     ->  迭代：check -> feedback -> check_convergence() -> 收敛即 break，\n
                  受 max_iterations / max_total_llm_calls / max_total_latency_s 三重兜底。\n
    legacy   ->  等同现状 pass-through，网关放行，不改变原调用行为（向后兼容）。\n

去重：按 error_signature 的 SHA256 hash 作为触发键，命中缓存直接跳过（dedup）。

纯决策 + 依赖注入：LLM checker / 收敛函数 / dedup 存储 / 指标存储全部可注入 mock，
单测无需真实 LLM 或数据库。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from packages.agent.reflection_metrics import (
    ReflectionMetricsStore,
    record_reflection_use,
)
from packages.agent.reflection_policy import ReflectionPolicy

# ---------------------------------------------------------------------------
# 触发事件常量（TriggerEvent）
# ---------------------------------------------------------------------------

#: 任务进行中连续多次失败（前瞻性触发）。
TRIGGER_CONSECUTIVE_FAILURE = "consecutive_failure"
#: 结果失真 / 输出质量不可信。
TRIGGER_DISTORTION = "distortion"
#: 下游差评 / 人工反馈。
TRIGGER_NEGATIVE_FEEDBACK = "negative_feedback"
#: 任务整体失败后的回看纠错。
TRIGGER_TASK_FAILURE = "task_failure"
TRIGGER_EVENTS: frozenset[str] = frozenset(
    {
        TRIGGER_CONSECUTIVE_FAILURE,
        TRIGGER_DISTORTION,
        TRIGGER_NEGATIVE_FEEDBACK,
        TRIGGER_TASK_FAILURE,
    }
)


# ---------------------------------------------------------------------------
# 决策结果
# ---------------------------------------------------------------------------


@dataclass
class GateDecision:
    """ReflectionGate.decide() 的返回结果。"""

    #: 采取的动作：pass（放行，零 LLM）/ check（执行反思检查）/ skip（去重或 off 拦截）。
    action: str  # "pass" | "check" | "skip"
    #: 生效的反思深度（解析后）：full | light | off | legacy。
    depth: str
    #: 人类可读的决策理由。
    reason: str
    #: 本次触发是否命中去重缓存（未实际调用 LLM）。
    deduped: bool = False
    #: 是否升级到了大模型复核。
    escalated: bool = False
    #: 反思收敛轮数。
    rounds: int = 0
    #: 消耗的 token 总数。
    tokens: int = 0
    #: 消耗的时延（毫秒）。
    latency_ms: float = 0.0
    #: 是否收敛。
    converged: bool = False
    #: 收敛/终止原因。
    convergence_reason: str = ""
    #: 反思输出文本（light 校验结果 / full 迭代后的结果）。
    output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "depth": self.depth,
            "reason": self.reason,
            "deduped": self.deduped,
            "escalated": self.escalated,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "latency_ms": round(self.latency_ms, 2),
            "converged": self.converged,
            "convergence_reason": self.convergence_reason,
        }


# ---------------------------------------------------------------------------
# 依赖注入类型
# ---------------------------------------------------------------------------

#: 小模型 one-pass 校验器：输入 prompt，返回 (文本, 置信度 0-1)。None=不可用。
SmallChecker = Callable[[str], Awaitable[tuple[str, float | None]]]
#: 反馈器：输入 prompt + 当前输出，返回反馈文本。
FeedbackFn = Callable[[str, str], Awaitable[str]]
#: 收敛判定：复用 guardrails/convergence.check_convergence()。
ConvergenceFn = Callable[..., Awaitable[tuple[bool, str]]]


def _sha256(signature: str) -> str:
    return hashlib.sha256((signature or "").encode("utf-8")).hexdigest()


#: 内存去重存储（线程安全）。单测 / 无 DB 场景可用。
class InMemoryDedupStore:
    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def contains(self, signature: str) -> bool:
        return _sha256(signature) in self._seen

    def add(self, signature: str) -> None:
        self._seen[_sha256(signature)] = time.time()

    def clear(self) -> None:
        self._seen.clear()


class NoopDedupStore:
    """关闭去重时使用的空存储（永远不命中）。"""

    def contains(self, signature: str) -> bool:
        return False

    def add(self, signature: str) -> None:
        return None


class _DefaultSmallChecker:
    """默认 small-model one-pass 校验器（经 packages.platform，纯 mock 可注入）。"""

    def __init__(self, policy: ReflectionPolicy) -> None:
        self._policy = policy

    async def __call__(self, prompt: str) -> tuple[str, float | None]:
        from packages.platform import forward_with_model_router

        model = self._policy.model_for("light") or self._policy.default_depth
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a quality checker. Validate correctness."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        route = await forward_with_model_router(payload)
        if route.status != 200 or not route.body:
            return "", None
        choices = route.body.get("choices") or []
        if not choices:
            return "", None
        text = (choices[0].get("message") or {}).get("content") or ""
        # 无显式置信度字段，回退 None（调用方按 fail-open 处理）。
        conf = (choices[0].get("message") or {}).get("score")
        return text, conf


# ---------------------------------------------------------------------------
# 网关
# ---------------------------------------------------------------------------


@dataclass
class RuntimeDeps:
    """可注入的运行时依赖（全部 mock 可替换）。"""

    small_checker: Callable[[str], Awaitable[tuple[str, float | None]]] | None = None
    feedback_fn: FeedbackFn | None = None
    convergence_fn: ConvergenceFn | None = None
    big_checker: Callable[[str], Awaitable[tuple[str, float | None]]] | None = None
    stats: ReflectionMetricsStore | None = None
    now_ms: Callable[[], float] = field(default=lambda: time.time() * 1000)
    #: 每次 LLM 调用的 token 估算回调（默认按文本长度粗估）。
    tokens_fn: Callable[[str], int] = field(default=lambda text: max(1, len(text) // 4))


class ReflectionGate:
    """统一反思网关。

    Args:
        policy: ReflectionPolicy（深度解析 + 资源上限）。
        deps: 可注入运行时依赖。默认使用内置 small checker 与空去重。
        dedup_store: 去重存储（默认 InMemoryDedupStore）。
    """

    def __init__(
        self,
        policy: ReflectionPolicy | None = None,
        deps: RuntimeDeps | None = None,
        dedup_store: Any | None = None,
    ) -> None:
        self.policy = policy or ReflectionPolicy()
        self.deps = deps if deps is not None else RuntimeDeps()
        self._dedup = dedup_store or InMemoryDedupStore()

    # -- 外部入口 -----------------------------------------------------------

    async def decide(
        self,
        depth: str | None = None,
        trigger_event: str | None = None,
        error_signature: str = "",
        *,
        prompt: str = "",
        current_output: str = "",
    ) -> GateDecision:
        """对一处反思触发做决策。

        Args:
            depth: 任务/工具声明的反思深度（None/默认 → policy.default_depth）。
            trigger_event: 触发事件（TRIGGER_*）。
            error_signature: 错误模式签名（用于 SHA256 去重触发）。
            prompt: 原始任务 prompt（反思检查上下文）。
            current_output: 当前输出（full 迭代的起点）。

        Returns:
            GateDecision。off/legacy 返回 pass（零 LLM）；light single-pass；
            full 迭代至收敛或撞三重兜底。
        """
        resolved = self.policy.resolve(depth)
        started = self.deps.now_ms()

        # ---- off：零 LLM 直通 -------------------------------------------------
        if resolved == "off":
            decision = GateDecision(
                action="pass",
                depth="off",
                reason="reflection_depth=off: no reflection LLM consumed",
            )
            self._record_use(decision, trigger_event, error_signature, started)
            return decision

        # ---- legacy：pass-through 现状（网关不干预） --------------------------
        if resolved == "legacy":
            decision = GateDecision(
                action="pass",
                depth="legacy",
                reason="reflection_depth=legacy: pass-through (backward compat)",
            )
            self._record_use(decision, trigger_event, error_signature, started)
            return decision

        # ---- 去重：命中缓存即跳过（不付反思成本） -----------------------------
        if self.policy.dedup_enabled and error_signature:
            if self._dedup.contains(error_signature):
                decision = GateDecision(
                    action="skip",
                    depth=resolved,
                    reason=f"dedup hit for error_signature hash {_sha256(error_signature)[:8]}",
                    deduped=True,
                )
                self._record_use(decision, trigger_event, error_signature, started)
                return decision
            self._dedup.add(error_signature)

        # ---- light：single-pass 小模型 + 置信度闸门 ---------------------------
        if resolved == "light":
            decision = await self._run_light(
                trigger_event=trigger_event,
                error_signature=error_signature,
                prompt=prompt,
                current_output=current_output,
                started=started,
            )
            self._record_use(decision, trigger_event, error_signature, started)
            return decision

        # ---- full：多轮迭代 + 收敛判停 + 三重兜底 -----------------------------
        decision = await self._run_full(
            trigger_event=trigger_event,
            error_signature=error_signature,
            prompt=prompt,
            current_output=current_output,
            started=started,
        )
        self._record_use(decision, trigger_event, error_signature, started)
        return decision

    # -- light 路径 -----------------------------------------------------------

    async def _run_light(
        self,
        *,
        trigger_event: str | None,
        error_signature: str,
        prompt: str,
        current_output: str,
        started: float,
    ) -> GateDecision:
        profile = self.policy.profile("light")
        checker = self.deps.small_checker or _DefaultSmallChecker(self.policy)

        max_latency_ms = profile.max_total_latency_s * 1000

        def latency_left() -> float:
            return max(0.0, max_latency_ms - (self.deps.now_ms() - started))

        try:
            # 小模型 one-pass 即时校验（带时延硬超时 fail-open）。
            if latency_left() <= 0:
                return self._fail_open(
                    depth="light",
                    reason="light latency budget exhausted before check",
                )
            text, confidence = await checker(prompt)
        except Exception:
            # 小模型调用失败/超时 → fail-open（不阻塞主流程）。
            return self._fail_open(
                depth="light",
                reason="light small-model check failed/timeout, fail-open",
            )

        tokens = self.deps.tokens_fn(text or "")
        rounded = 1
        escalated = False

        # 置信度闸门：低置信度（含未知 None）→ 升级大模型复核。
        threshold = self.policy.confidence_threshold
        if confidence is None or confidence < threshold:
            escalated = True
            text, tokens = await self._escalate(
                prompt,
                current_output,
                text,
                profile=profile,
                started=started,
            )

        return GateDecision(
            action="check",
            depth="light",
            reason="light single-pass check passed"
            if not escalated
            else "light low-confidence escalated to big model",
            escalated=escalated,
            rounds=rounded,
            tokens=tokens,
            latency_ms=self.deps.now_ms() - started,
            output=text,
        )

    async def _escalate(
        self,
        prompt: str,
        current_output: str,
        small_text: str,
        *,
        profile: Any,
        started: float,
    ) -> tuple[str, int]:
        """低置信度时升级大模型复核。返回 (text, tokens)。复核失败/超时 fail-open（放行）。"""
        big_checker = self.deps.big_checker or _DefaultSmallChecker(self.policy)
        try:
            text, _conf = await big_checker(prompt)
            tokens = self.deps.tokens_fn(text or "")
            # 大模型复核失败/返回空 → 保底采用小模型输出（fail-open），不拦截正常链路。
            if not text:
                return small_text, tokens
            return text, tokens
        except Exception:
            return small_text, 0

    # -- full 路径 -------------------------------------------------------------

    async def _run_full(
        self,
        *,
        trigger_event: str | None,
        error_signature: str,
        prompt: str,
        current_output: str,
        started: float,
    ) -> GateDecision:
        profile = self.policy.profile("full")
        if not self.deps.small_checker or not self.deps.feedback_fn or not self.deps.convergence_fn:
            # 未注入迭代依赖 → 退化为 light single-pass（fail-safe）。
            return await self._run_light(
                trigger_event=trigger_event,
                error_signature=error_signature,
                prompt=prompt,
                current_output=current_output,
                started=started,
            )

        max_iter = profile.max_iterations
        max_calls = profile.max_total_llm_calls
        max_latency_ms = profile.max_total_latency_s * 1000
        output = current_output
        converged = False
        converged_reason = "max_iterations"

        # 共享 LLM 调用计数器（含 convergence judge 内部调用）。
        counter: list[int] = [0]

        async def _conv(
            c_out: str,
            p_out: str,
            fb: str,
        ) -> tuple[bool, str]:
            return await self.deps.convergence_fn(
                strategy="hybrid",
                threshold=self.policy.confidence_threshold,
                current_output=c_out,
                previous_output=p_out,
                latest_feedback=fb,
                model=None,
                counter=counter,
            )

        for iteration in range(1, max_iter + 1):
            # 三重兜底：LLM 次数上限 / 累计时延超时（迭代上限由 for 本身兜底）。
            if counter[0] >= max_calls:
                converged_reason = "max_calls"
                break
            if (self.deps.now_ms() - started) >= max_latency_ms:
                converged_reason = "max_latency"
                break

            # 校验 + 反馈
            check_text, _check_conf = await self.deps.small_checker(prompt)
            counter[0] += 1
            fb = await self.deps.feedback_fn(prompt, output)
            counter[0] += 1

            if "NO_IMPROVEMENT_NEEDED" in (fb or "").strip().upper() or not fb.strip():
                converged = True
                converged_reason = "no_improvement_needed"
                break

            # 收敛判停（复用 guardrails/convergence.check_convergence）。
            conv, reason = await _conv(c_out=check_text, p_out=output, fb=fb)
            if conv:
                converged = True
                converged_reason = reason
                output = check_text
                break

            output = check_text

        tokens = self.deps.tokens_fn(output or "") + max(0, counter[0]) * 8
        return GateDecision(
            action="check",
            depth="full",
            reason=f"full iterate finished; converged={converged} ({converged_reason})",
            rounds=min(iteration, max_iter),
            tokens=tokens,
            latency_ms=self.deps.now_ms() - started,
            converged=converged,
            convergence_reason=converged_reason,
            output=output,
        )

    # -- helpers ---------------------------------------------------------------

    def _fail_open(self, *, depth: str, reason: str) -> GateDecision:
        return GateDecision(
            action="pass",
            depth=depth,
            reason=f"{reason}; fail-open (proceed without reflection)",
            latency_ms=0.0,
        )

    def _record_use(
        self,
        decision: GateDecision,
        trigger_event: str | None,
        error_signature: str,
        started: float,
    ) -> None:
        if self.deps.stats is None:
            return
        try:
            record_reflection_use(
                self.deps.stats,
                reason=trigger_event or "unknown",
                depth=decision.depth,
                action=decision.action,
                tokens=decision.tokens,
                latency_ms=decision.latency_ms,
                rounds=decision.rounds,
                deduped=decision.deduped,
                escalated=decision.escalated,
            )
        except Exception:
            # 指标记录失败绝不影响主流程放行。
            pass


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------


def default_gate() -> ReflectionGate:
    """返回一个使用内置默认依赖的 ReflectionGate（生产默认）。"""
    return ReflectionGate()
