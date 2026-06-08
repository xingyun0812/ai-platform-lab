from __future__ import annotations

import logging
from typing import Any

import httpx

from apps.gateway.settings import get_settings

logger = logging.getLogger("ai_platform.gateway.llm")


async def forward_chat_completions(
    payload: dict[str, Any],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """
    调用上游 OpenAI 兼容 /chat/completions（非流式）。
    返回 (http_status, json_body_or_none, error_text_or_none)。
    """
    settings = get_settings()
    key = (api_key or settings.llm_api_key or "").strip()
    if not key:
        return 503, None, "LLM_API_KEY 未配置：申请到账号后写入 .env 即可联调"

    base = (base_url or settings.llm_base_url).rstrip("/")
    url = f"{base}/chat/completions"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    last_err: str | None = None
    max_retries = max(0, settings.upstream_max_retries)
    timeout = httpx.Timeout(settings.upstream_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries + 1):
            try:
                r = await client.post(url, json=payload, headers=headers)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    last_err = f"upstream {r.status_code}: {r.text[:500]}"
                    logger.warning("upstream retryable status=%s attempt=%s", r.status_code, attempt)
                    continue
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": r.text[:2000]}
                if r.is_success:
                    return r.status_code, body if isinstance(body, dict) else {"result": body}, None
                return r.status_code, body if isinstance(body, dict) else None, r.text[:2000]
            except httpx.RequestError as e:
                last_err = str(e)
                if attempt < max_retries:
                    logger.warning("upstream request error attempt=%s err=%s", attempt, e)
                    continue
                return 503, None, last_err

    return 503, None, last_err or "unknown error"
