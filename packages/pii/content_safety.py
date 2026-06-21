"""内容安全检查器 — Phase I #43

当前实现：基于关键词列表的规则检查（stub for LLM moderation API）。
生产建议：集成 OpenAI Moderation API / Azure Content Safety。

分类：hate | violence | sexual | self_harm
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_platform.pii.content_safety")


# ---------------------------------------------------------------------------
# 默认关键词（示例，非真实敏感词库）
# ---------------------------------------------------------------------------

_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "hate": ["hate speech", "racial slur", "discriminat"],
    "violence": ["kill", "murder", "attack", "bomb", "weapon"],
    "sexual": ["explicit sexual", "pornograph", "nsfw"],
    "self_harm": ["suicide", "self harm", "self-harm", "cut myself"],
}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ContentSafetyResult:
    """内容安全检查结果。"""

    safe: bool
    categories: dict[str, bool] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "categories": self.categories,
            "scores": self.scores,
            "blocked_reason": self.blocked_reason,
        }


# ---------------------------------------------------------------------------
# ContentSafetyChecker
# ---------------------------------------------------------------------------


class ContentSafetyChecker:
    """内容安全检查器。线程安全。"""

    def __init__(self, yaml_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        # 按分类存储关键词
        self._keywords: dict[str, list[str]] = {k: list(v) for k, v in _DEFAULT_KEYWORDS.items()}

        if yaml_path and Path(yaml_path).exists():
            self._load_yaml(Path(yaml_path))

    # ------------------------------------------------------------------

    def _load_yaml(self, path: Path) -> None:
        try:
            import yaml

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                for cat, words in data.items():
                    if isinstance(words, list):
                        self._keywords.setdefault(cat, [])
                        self._keywords[cat].extend(words)
        except Exception as e:
            logger.warning("Failed to load safety keywords YAML %s: %s", path, e)

    # ------------------------------------------------------------------

    def check(self, text: str) -> ContentSafetyResult:
        """关键词规则检查（stub for LLM moderation）。"""
        text_lower = text.lower()
        categories: dict[str, bool] = {}
        scores: dict[str, float] = {}
        flagged_cats: list[str] = []

        with self._lock:
            kw_snapshot = {k: list(v) for k, v in self._keywords.items()}

        for cat, words in kw_snapshot.items():
            hit_count = sum(1 for w in words if w.lower() in text_lower)
            flag = hit_count > 0
            categories[cat] = flag
            scores[cat] = min(1.0, hit_count * 0.33)
            if flag:
                flagged_cats.append(cat)

        safe = len(flagged_cats) == 0
        blocked_reason = None if safe else f"Flagged categories: {', '.join(flagged_cats)}"

        return ContentSafetyResult(
            safe=safe,
            categories=categories,
            scores=scores,
            blocked_reason=blocked_reason,
        )

    def register_keyword(self, word: str, category: str = "custom") -> None:
        """动态注册关键词。"""
        with self._lock:
            self._keywords.setdefault(category, [])
            if word not in self._keywords[category]:
                self._keywords[category].append(word)

    def list_keywords(self) -> dict[str, list[str]]:
        with self._lock:
            return {k: list(v) for k, v in self._keywords.items()}


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_checker: ContentSafetyChecker | None = None
_checker_lock = threading.Lock()


def init_safety_checker(yaml_path: Path | None = None) -> ContentSafetyChecker:
    global _checker
    with _checker_lock:
        _checker = ContentSafetyChecker(yaml_path=yaml_path)
        return _checker


def get_safety_checker() -> ContentSafetyChecker | None:
    return _checker


def reset_safety_checker_for_tests() -> None:
    global _checker
    with _checker_lock:
        _checker = None
