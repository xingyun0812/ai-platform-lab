"""反思深度策略（ReflectionPolicy）— 配置驱动的反思成本分级判定。

纯配置模块，零外部依赖（不依赖 LLM / DB / gateway）。
按 PRD-0010 / ADR-0010，每个任务/工具通过配置声明 ``reflection_depth``，
未声明回退 ``default_depth``（默认 ``light``，fail-safe 不破坏实时链路）。

Depth 语义：
    off      — 完全关闭反思（零 LLM 消耗）。
    light    — 同步 small-model one-pass 即时校验（低时延，本次任务即时纠错）。
    full     — 多轮迭代反思（校验 → 反馈 → 收敛判停），受三重兜底。
    legacy   — 等同现状（pass-through = 现有 self_refine/self_evolve 原行为），
              用于向后兼容：网关放行，不改变原有迭代深度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_DEPTHS = frozenset({"full", "light", "off", "legacy"})
DEFAULT_DEPTH = "light"


@dataclass(frozen=True)
class DepthProfile:
    """某一反思深度的资源/超时配置。

    Attributes:
        model: 该深度反思链路的低成本模型（None = 复用主推理模型）。
        max_iterations: 最大迭代轮数。
        max_total_llm_calls: 单次触发硬上限 LLM 调用次数。
        max_total_latency_s: 累计时延硬超时（秒），超过即提前终止。
        async_offload: 该深度是否异步后台执行（不阻塞实时响应）。
    """

    model: str | None = None
    max_iterations: int = 3
    max_total_llm_calls: int = 10
    max_total_latency_s: float = 60.0
    async_offload: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_iterations": self.max_iterations,
            "max_total_llm_calls": self.max_total_llm_calls,
            "max_total_latency_s": self.max_total_latency_s,
            "async_offload": self.async_offload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DepthProfile:
        return cls(
            model=data.get("model"),
            max_iterations=int(data.get("max_iterations", 3)),
            max_total_llm_calls=int(data.get("max_total_llm_calls", 10)),
            max_total_latency_s=float(data.get("max_total_latency_s", 60.0)),
            async_offload=bool(data.get("async_offload", False)),
        )


def _default_profile(depth: str) -> DepthProfile:
    """未配置某深度时的内置默认 profile。"""
    if depth == "off":
        # off 深度不做任何 LLM，资源上限趋零。
        return DepthProfile(
            model=None,
            max_iterations=0,
            max_total_llm_calls=0,
            max_total_latency_s=0.0,
            async_offload=False,
        )
    if depth == "light":
        return DepthProfile(
            model=None,
            max_iterations=1,
            max_total_llm_calls=1,
            max_total_latency_s=15.0,
            async_offload=False,
        )
    if depth == "full":
        return DepthProfile(
            model=None,
            max_iterations=5,
            max_total_llm_calls=15,
            max_total_latency_s=120.0,
            async_offload=True,
        )
    # legacy = pass-through 现状，不限资源（由原调用方自管）。
    return DepthProfile(
        model=None,
        max_iterations=5,
        max_total_llm_calls=30,
        max_total_latency_s=3600.0,
        async_offload=False,
    )


@dataclass
class ReflectionPolicy:
    """反思深度策略。

    Attributes:
        default_depth: 未声明深度时的回退值（默认 'light'）。
        per_depth: 每个深度的资源/超时 profile 映射。
        confidence_threshold: 小模型裁定的置信度闸门阈值（0-1）。
        dedup_enabled: 是否按错误模式 SHA256 hash 去重触发。
    """

    default_depth: str = DEFAULT_DEPTH
    per_depth: dict[str, DepthProfile] = field(
        default_factory=lambda: {d: _default_profile(d) for d in VALID_DEPTHS}
    )
    confidence_threshold: float = 0.85
    dedup_enabled: bool = True

    def __post_init__(self) -> None:
        if self.default_depth not in VALID_DEPTHS:
            raise ValueError(
                f"invalid default_depth: {self.default_depth!r}, "
                f"expected one of {sorted(VALID_DEPTHS)}"
            )
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(f"confidence_threshold must be 0-1, got {self.confidence_threshold}")
        # 保证每个合法深度都有 profile 可寻，回退到内置默认。
        for d in VALID_DEPTHS:
            if d not in self.per_depth:
                self.per_depth[d] = _default_profile(d)

    def resolve(self, depth: str | None = None) -> str:
        """解析实际生效的反思深度。

        Args:
            depth: 任务/工具声明的深度。None/空 → 回退 default_depth。

        Returns:
            生效深度（"full" | "light" | "off" | "legacy"）。

        Raises:
            ValueError: 声明了非法深度值。
        """
        if depth is None or depth == "" or depth == "default":
            return self.default_depth
        if depth not in VALID_DEPTHS:
            raise ValueError(
                f"invalid reflection_depth: {depth!r}, expected one of {sorted(VALID_DEPTHS)}"
            )
        return depth

    def profile(self, depth: str | None = None) -> DepthProfile:
        """返回某（已解析）深度的资源 profile。"""
        resolved = self.resolve(depth)
        return self.per_depth.get(resolved, _default_profile(resolved))

    # -- 便捷查询 -----------------------------------------------------------

    def is_enabled(self, depth: str | None = None) -> bool:
        """该深度是否消耗 LLM（off 为 False；legacy/light/full 为 True）。"""
        return self.resolve(depth) != "off"

    def is_off(self, depth: str | None = None) -> bool:
        return self.resolve(depth) == "off"

    def is_async(self, depth: str | None = None) -> bool:
        """该深度是否异步 offload（不阻塞实时响应）。"""
        return self.profile(depth).async_offload

    def model_for(self, depth: str | None = None) -> str | None:
        """该深度的低成本模型（None = 复用主模型）。"""
        return self.profile(depth).model

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_depth": self.default_depth,
            "per_depth": {d: p.to_dict() for d, p in self.per_depth.items()},
            "confidence_threshold": self.confidence_threshold,
            "dedup_enabled": self.dedup_enabled,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None = None,
        *,
        default_depth: str | None = None,
    ) -> ReflectionPolicy:
        """从配置 dict 加载策略（兼容 PRD ``reflection`` 配置段）。

        Args:
            data: raw 配置 dict。支持的键：
                default_depth, confidence_threshold, dedup_enabled,
                full / light / off / legacy（各深度 profile dict）。
            default_depth: 显式覆盖 default_depth（优先级最高）。

        Returns:
            ReflectionPolicy 实例。缺失的键回退内置默认。
        """
        data = dict(data or {})
        depth_default = default_depth or data.get("default_depth", DEFAULT_DEPTH)

        # 兼容两种配置形态：top-level 的 full/light/off/legacy 键（PRD 配置段），
        # 以及 to_dict() 输出的 nested ``per_depth`` 形态。
        depth_sources: dict[str, dict[str, Any]] = {}
        nested = data.get("per_depth")
        if isinstance(nested, dict):
            for d in VALID_DEPTHS:
                if isinstance(nested.get(d), dict):
                    depth_sources[d] = nested[d]
        for d in VALID_DEPTHS:
            if isinstance(data.get(d), dict):
                depth_sources[d] = data[d]

        per_depth: dict[str, DepthProfile] = {
            d: DepthProfile.from_dict(cfg) for d, cfg in depth_sources.items()
        }

        return cls(
            default_depth=depth_default,
            per_depth=per_depth or {d: _default_profile(d) for d in VALID_DEPTHS},
            confidence_threshold=float(data.get("confidence_threshold", 0.85)),
            dedup_enabled=bool(data.get("dedup_enabled", True)),
        )

    @classmethod
    def load(
        cls,
        *,
        default_depth: str | None = None,
        reflection_override: dict[str, Any] | None = None,
    ) -> ReflectionPolicy:
        """零依赖加载：优先反射注入的配置，否则回退内置默认。

        后续可经由 ``packages.platform.get_settings()`` 的 reflection 配置段接线，
        本方法保持纯逻辑、可单测。
        """
        return cls.from_dict(reflection_override, default_depth=default_depth)
