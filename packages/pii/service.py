"""PII 服务门面 — Phase I #43

整合 PIIDetector + Redactor + ContentSafetyChecker，提供统一的异步接口。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from packages.pii.content_safety import (
    ContentSafetyChecker,
    ContentSafetyResult,
    init_safety_checker,
    get_safety_checker,
)
from packages.pii.detectors import (
    PIIDetector,
    PIIMatch,
    init_detector,
    get_detector,
)
from packages.pii.redactor import (
    Redactor,
    RedactionResult,
    init_redactor,
    get_redactor,
)

logger = logging.getLogger("ai_platform.pii.service")


class PIIService:
    """PII 服务：检测 + 脱敏 + 内容安全。"""

    def __init__(
        self,
        detector: PIIDetector,
        redactor: Redactor,
        safety: ContentSafetyChecker,
    ) -> None:
        self._detector = detector
        self._redactor = redactor
        self._safety = safety

    # ------------------------------------------------------------------
    # 异步接口
    # ------------------------------------------------------------------

    async def detect_pii(self, text: str) -> list[PIIMatch]:
        """检测 PII，返回命中列表。"""
        return self._detector.detect(text)

    async def redact_pii(
        self, text: str, policy_id: str = "default"
    ) -> RedactionResult:
        """脱敏文本。"""
        return self._redactor.redact(text, policy_id=policy_id)

    async def check_safety(self, text: str) -> ContentSafetyResult:
        """检查内容安全。"""
        return self._safety.check(text)

    async def process(
        self,
        text: str,
        policy_id: str = "default",
        check_safety: bool = True,
    ) -> dict[str, Any]:
        """完整流水线：检测 PII + 脱敏 + 内容安全检查。"""
        pii_matches = await self.detect_pii(text)
        redaction_result = await self.redact_pii(text, policy_id=policy_id)
        safety_result: ContentSafetyResult | None = None
        if check_safety:
            safety_result = await self.check_safety(text)

        return {
            "original": text,
            "pii_detected": len(pii_matches) > 0,
            "pii_matches": [m.to_dict() for m in pii_matches],
            "redaction": redaction_result.to_dict(),
            "safety": safety_result.to_dict() if safety_result is not None else None,
        }


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_service: PIIService | None = None
_service_lock = threading.Lock()


def init_pii_service(
    detector_yaml: Path | None = None,
    detector_overrides: Path | None = None,
    safety_yaml: Path | None = None,
) -> PIIService:
    global _service
    with _service_lock:
        detector = init_detector(yaml_path=detector_yaml, overrides_path=detector_overrides)
        redactor = init_redactor()
        safety = init_safety_checker(yaml_path=safety_yaml)
        _service = PIIService(detector=detector, redactor=redactor, safety=safety)
        return _service


def get_pii_service() -> PIIService | None:
    return _service


def reset_for_tests() -> None:
    global _service
    with _service_lock:
        _service = None
    from packages.pii.detectors import reset_detector_for_tests
    from packages.pii.redactor import reset_redactor_for_tests
    from packages.pii.content_safety import reset_safety_checker_for_tests

    reset_detector_for_tests()
    reset_redactor_for_tests()
    reset_safety_checker_for_tests()
