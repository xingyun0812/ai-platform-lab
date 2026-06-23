"""PII 检测器 — Phase I #43

提供基于正则表达式的 PII 检测，支持 YAML 配置 + JSON 覆盖。

默认内置模式：
    email, phone_us, ssn, credit_card, ipv4, cn_id_card, cn_phone
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_platform.pii.detectors")

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class PIIPattern:
    """PII 检测规则。"""

    pattern_id: str
    name: str
    regex: str
    entity_type: str  # email|phone|ssn|credit_card|ip|address|name|id_card
    confidence: float = 0.9
    redaction_template: str = "[REDACTED]"
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "regex": self.regex,
            "entity_type": self.entity_type,
            "confidence": self.confidence,
            "redaction_template": self.redaction_template,
            "enabled": self.enabled,
        }


@dataclass
class PIIMatch:
    """单个 PII 命中结果。"""

    pattern_id: str
    start: int
    end: int
    matched_text: str
    entity_type: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "start": self.start,
            "end": self.end,
            "matched_text": self.matched_text,
            "entity_type": self.entity_type,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# 默认内置模式
# ---------------------------------------------------------------------------

_DEFAULT_PATTERNS: list[PIIPattern] = [
    PIIPattern(
        pattern_id="email",
        name="Email Address",
        regex=r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        entity_type="email",
        confidence=0.95,
        redaction_template="[REDACTED_EMAIL]",
    ),
    PIIPattern(
        pattern_id="phone_us",
        name="US Phone Number",
        regex=r"(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        entity_type="phone",
        confidence=0.85,
        redaction_template="[REDACTED_PHONE]",
    ),
    PIIPattern(
        pattern_id="ssn",
        name="Social Security Number",
        regex=r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
        entity_type="ssn",
        confidence=0.95,
        redaction_template="[REDACTED_SSN]",
    ),
    PIIPattern(
        pattern_id="credit_card",
        name="Credit Card Number",
        regex=r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12}|(?:2131|1800|35\d{3})\d{11})\b",
        entity_type="credit_card",
        confidence=0.92,
        redaction_template="[REDACTED_CC]",
    ),
    PIIPattern(
        pattern_id="ipv4",
        name="IPv4 Address",
        regex=r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
        entity_type="ip",
        confidence=0.9,
        redaction_template="[REDACTED_IP]",
    ),
    PIIPattern(
        pattern_id="cn_id_card",
        name="Chinese ID Card",
        regex=r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        entity_type="id_card",
        confidence=0.95,
        redaction_template="[REDACTED_ID_CARD]",
    ),
    PIIPattern(
        pattern_id="cn_phone",
        name="Chinese Phone Number",
        regex=r"(?<!\d)1[3-9]\d{9}(?!\d)",
        entity_type="phone",
        confidence=0.9,
        redaction_template="[REDACTED_PHONE]",
    ),
]


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------


class PIIDetector:
    """PII 检测器。线程安全，支持运行时注册/删除模式。"""

    def __init__(
        self,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._patterns: dict[str, PIIPattern] = {}
        self._compiled: dict[str, re.Pattern[str]] = {}
        self._overrides_path = overrides_path

        # 加载默认模式
        for p in _DEFAULT_PATTERNS:
            self._patterns[p.pattern_id] = p

        # 从 YAML 加载
        if yaml_path and Path(yaml_path).exists():
            self._load_yaml(Path(yaml_path))

        # 从 JSON 覆盖
        if overrides_path and Path(overrides_path).exists():
            self._load_overrides(Path(overrides_path))

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def _load_yaml(self, path: Path) -> None:
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, list):
                for item in data:
                    self._upsert_from_dict(item)
        except Exception as e:
            logger.warning("Failed to load PII patterns YAML %s: %s", path, e)

    def _load_overrides(self, path: Path) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    self._upsert_from_dict(item)
        except Exception as e:
            logger.warning("Failed to load PII patterns overrides %s: %s", path, e)

    def _upsert_from_dict(self, item: dict[str, Any]) -> None:
        pid = item.get("pattern_id")
        if not pid:
            return
        existing = self._patterns.get(pid)
        if existing:
            self._patterns[pid] = PIIPattern(
                pattern_id=pid,
                name=item.get("name", existing.name),
                regex=item.get("regex", existing.regex),
                entity_type=item.get("entity_type", existing.entity_type),
                confidence=float(item.get("confidence", existing.confidence)),
                redaction_template=item.get("redaction_template", existing.redaction_template),
                enabled=item.get("enabled", existing.enabled),
            )
        else:
            self._patterns[pid] = PIIPattern(
                pattern_id=pid,
                name=item.get("name", pid),
                regex=item.get("regex", ""),
                entity_type=item.get("entity_type", "custom"),
                confidence=float(item.get("confidence", 0.8)),
                redaction_template=item.get("redaction_template", "[REDACTED]"),
                enabled=item.get("enabled", True),
            )
        # 清除已编译缓存
        self._compiled.pop(pid, None)

    # ------------------------------------------------------------------
    # 懒编译
    # ------------------------------------------------------------------

    def _get_compiled(self, pattern_id: str) -> re.Pattern[str] | None:
        if pattern_id in self._compiled:
            return self._compiled[pattern_id]
        p = self._patterns.get(pattern_id)
        if p is None or not p.regex:
            return None
        try:
            compiled = re.compile(p.regex, re.IGNORECASE)
            self._compiled[pattern_id] = compiled
            return compiled
        except re.error as e:
            logger.warning("Invalid regex for pattern %s: %s", pattern_id, e)
            return None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def detect(self, text: str) -> list[PIIMatch]:
        """检测文本中所有 PII，返回按 start 排序的列表。"""
        results: list[PIIMatch] = []
        with self._lock:
            patterns = list(self._patterns.values())
        for p in patterns:
            if not p.enabled:
                continue
            compiled = self._get_compiled(p.pattern_id)
            if compiled is None:
                continue
            for m in compiled.finditer(text):
                results.append(
                    PIIMatch(
                        pattern_id=p.pattern_id,
                        start=m.start(),
                        end=m.end(),
                        matched_text=m.group(),
                        entity_type=p.entity_type,
                        confidence=p.confidence,
                    )
                )
        results.sort(key=lambda x: x.start)
        return results

    def detect_all(self, text: str) -> dict[str, list[PIIMatch]]:
        """检测并按 entity_type 分组。"""
        matches = self.detect(text)
        grouped: dict[str, list[PIIMatch]] = {}
        for m in matches:
            grouped.setdefault(m.entity_type, []).append(m)
        return grouped

    def register_pattern(self, pattern: PIIPattern) -> PIIPattern:
        """注册或覆盖一个 PII 模式。"""
        with self._lock:
            self._patterns[pattern.pattern_id] = pattern
            self._compiled.pop(pattern.pattern_id, None)
        return pattern

    def list_patterns(self) -> list[PIIPattern]:
        """返回所有已注册模式列表。"""
        with self._lock:
            return list(self._patterns.values())

    def remove_pattern(self, pattern_id: str) -> bool:
        """删除指定 pattern_id，返回是否成功。"""
        with self._lock:
            if pattern_id not in self._patterns:
                return False
            del self._patterns[pattern_id]
            self._compiled.pop(pattern_id, None)
            return True


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_detector: PIIDetector | None = None
_detector_lock = threading.Lock()


def init_detector(
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> PIIDetector:
    global _detector
    with _detector_lock:
        _detector = PIIDetector(yaml_path=yaml_path, overrides_path=overrides_path)
        return _detector


def get_detector() -> PIIDetector | None:
    return _detector


def reset_detector_for_tests() -> None:
    global _detector
    with _detector_lock:
        _detector = None
