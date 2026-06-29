from __future__ import annotations

import logging
from typing import Any

from packages.billing.db import get_billing_store
from packages.billing.usage import TokenUsage, parse_token_usage
from packages.platform import get_settings

logger = logging.getLogger("ai_platform.billing.recorder")


def record_upstream_usage(
    *,
    tenant_id: str,
    path: str,
    model: str | None,
    upstream_body: dict[str, Any] | None,
    trace_id: str | None = None,
) -> TokenUsage | None:
    """解析并落库一次 LLM 调用的 token 用量；失败时仅打日志。"""
    usage = parse_token_usage(upstream_body)
    if usage is None:
        return None
    store = get_billing_store(get_settings().database_url)
    if store is None:
        return usage
    try:
        store.insert_usage(
            tenant_id=tenant_id,
            path=path,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            trace_id=trace_id,
        )
    except Exception:
        logger.exception("usage insert failed tenant=%s path=%s", tenant_id, path)
    return usage
