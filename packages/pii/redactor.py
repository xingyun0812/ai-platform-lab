"""PII 脱敏器 — Phase I #43

支持四种脱敏动作：
    redact  — 用模板替换（默认：[REDACTED_EMAIL]）
    mask    — 保留首尾 N 位，中间用 mask_char 遮盖
    hash    — SHA256(salt+原文)[:8]
    block   — 若检测到任意 PII 则返回空字符串
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.pii.redactor")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class RedactionPolicy:
    """脱敏策略。"""

    policy_id: str
    entity_types: list[str]  # 空列表 = 匹配所有类型
    action: str  # redact | mask | hash | block
    mask_char: str = "*"
    keep_first: int = 2
    keep_last: int = 2
    hash_salt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "entity_types": self.entity_types,
            "action": self.action,
            "mask_char": self.mask_char,
            "keep_first": self.keep_first,
            "keep_last": self.keep_last,
        }


@dataclass
class RedactionResult:
    """脱敏结果。"""

    original: str
    redacted: str
    matches_count: int
    redactions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "redacted": self.redacted,
            "matches_count": self.matches_count,
            "redactions": self.redactions,
        }


# ---------------------------------------------------------------------------
# 默认策略
# ---------------------------------------------------------------------------

_DEFAULT_POLICIES: list[RedactionPolicy] = [
    RedactionPolicy(
        policy_id="default",
        entity_types=[],  # 匹配全部类型
        action="redact",
    ),
    RedactionPolicy(
        policy_id="mask",
        entity_types=["email", "phone"],
        action="mask",
        mask_char="*",
        keep_first=2,
        keep_last=2,
    ),
    RedactionPolicy(
        policy_id="strict",
        entity_types=[],  # 匹配全部类型
        action="block",
    ),
]


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------


class Redactor:
    """PII 脱敏执行器。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._policies: dict[str, RedactionPolicy] = {}
        for p in _DEFAULT_POLICIES:
            self._policies[p.policy_id] = p

    # ------------------------------------------------------------------
    # 内部脱敏逻辑
    # ------------------------------------------------------------------

    def _apply_action(
        self,
        matched_text: str,
        entity_type: str,
        redaction_template: str,
        policy: RedactionPolicy,
    ) -> str:
        action = policy.action
        if action == "redact":
            return redaction_template
        elif action == "mask":
            return self._mask(matched_text, policy.mask_char, policy.keep_first, policy.keep_last)
        elif action == "hash":
            raw = policy.hash_salt + matched_text
            return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        elif action == "block":
            return ""  # caller handles block logic
        else:
            return redaction_template

    @staticmethod
    def _mask(text: str, mask_char: str, keep_first: int, keep_last: int) -> str:
        n = len(text)
        if n <= keep_first + keep_last:
            return mask_char * n
        middle_len = n - keep_first - keep_last
        return text[:keep_first] + mask_char * middle_len + text[n - keep_last :]

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def redact(self, text: str, policy_id: str = "default") -> RedactionResult:
        """对文本应用指定策略进行脱敏。"""
        from packages.pii.detectors import get_detector, init_detector

        detector = get_detector()
        if detector is None:
            detector = init_detector()

        with self._lock:
            policy = self._policies.get(policy_id)
        if policy is None:
            # 回退到默认策略
            with self._lock:
                policy = self._policies.get("default")
        if policy is None:
            return RedactionResult(original=text, redacted=text, matches_count=0)

        matches = detector.detect(text)
        # 过滤到策略关心的 entity_type
        if policy.entity_types:
            matches = [m for m in matches if m.entity_type in policy.entity_types]

        if not matches:
            return RedactionResult(original=text, redacted=text, matches_count=0)

        # block 直接短路
        if policy.action == "block":
            return RedactionResult(
                original=text,
                redacted="",
                matches_count=len(matches),
                redactions=[
                    {
                        "entity_type": m.entity_type,
                        "original": m.matched_text,
                        "redaction": "[BLOCKED]",
                        "position": {"start": m.start, "end": m.end},
                    }
                    for m in matches
                ],
            )

        # 从后往前替换，避免位移问题
        patterns = {p.pattern_id: p for p in detector.list_patterns()}
        sorted_matches = sorted(matches, key=lambda x: x.start, reverse=True)
        result = text
        redactions: list[dict[str, Any]] = []
        for m in sorted_matches:
            pat = patterns.get(m.pattern_id)
            template = pat.redaction_template if pat else "[REDACTED]"
            replacement = self._apply_action(m.matched_text, m.entity_type, template, policy)
            redactions.insert(
                0,
                {
                    "entity_type": m.entity_type,
                    "original": m.matched_text,
                    "redaction": replacement,
                    "position": {"start": m.start, "end": m.end},
                },
            )
            result = result[: m.start] + replacement + result[m.end :]

        return RedactionResult(
            original=text,
            redacted=result,
            matches_count=len(matches),
            redactions=redactions,
        )

    def register_policy(self, policy: RedactionPolicy) -> RedactionPolicy:
        with self._lock:
            self._policies[policy.policy_id] = policy
        return policy

    def list_policies(self) -> list[RedactionPolicy]:
        with self._lock:
            return list(self._policies.values())


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_redactor: Redactor | None = None
_redactor_lock = threading.Lock()


def init_redactor() -> Redactor:
    global _redactor
    with _redactor_lock:
        _redactor = Redactor()
        return _redactor


def get_redactor() -> Redactor | None:
    return _redactor


def reset_redactor_for_tests() -> None:
    global _redactor
    with _redactor_lock:
        _redactor = None
