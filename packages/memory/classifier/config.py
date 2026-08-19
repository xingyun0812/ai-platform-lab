from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RulePatterns:
    noise_keywords: list[str] = field(
        default_factory=lambda: [
            "好的",
            "明白了",
            "got it",
            "hello",
            "hi",
            "嗯",
            "ok",
            "谢谢",
            "thanks",
            "是的",
            "对",
            "嗯嗯",
        ]
    )
    noise_max_length: int = 0  # 0 = disabled (quality_filter handles length check)
    preference_indicators: list[str] = field(
        default_factory=lambda: [
            "喜欢",
            "偏好",
            "prefer",
            "希望",
            "习惯",
            "不要",
            "别",
            "请",
            "always",
            "never",
        ]
    )
    factual_indicators: list[str] = field(
        default_factory=lambda: [
            "是",
            "运行在",
            "版本",
            "version",
            "部署在",
            "使用",
            "用的是",
            "地址",
            "端口",
            "端口号",
        ]
    )
    rule_confidence_threshold: float = 0.8
