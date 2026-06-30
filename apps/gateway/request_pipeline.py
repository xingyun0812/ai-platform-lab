"""主路径请求管线 — PII 预处理（Issue #182 / 架构 §9）。

默认 ``pii_main_path_enabled=false``，与 /internal/pii 门控对称。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.gateway.settings import Settings

logger = logging.getLogger("ai_platform.gateway.request_pipeline")


async def sanitize_main_path_text(
    text: str,
    settings: Settings,
) -> tuple[str, str | None]:
    """可选 PII 脱敏；返回 (处理后文本, 阻断原因)。

    阻断原因非空时调用方应返回 422。
    """
    if not settings.pii_main_path_enabled:
        return text, None
    if not settings.pii_service_enabled:
        return text, None

    from packages.pii.service import get_pii_service

    svc = get_pii_service()
    if svc is None:
        return text, None

    try:
        result = await svc.process(
            text,
            policy_id=settings.pii_default_policy,
            check_safety=True,
        )
    except Exception as exc:
        logger.warning("main path pii process failed: %s", exc)
        return text, None

    if settings.pii_block_on_safety_failure:
        safety = result.get("safety") or {}
        if isinstance(safety, dict) and not safety.get("safe", True):
            reason = safety.get("blocked_reason") or "内容未通过安全检查"
            return text, str(reason)

    redaction = result.get("redaction") or {}
    redacted = redaction.get("redacted")
    if isinstance(redacted, str) and redacted.strip():
        return redacted, None
    return text, None
