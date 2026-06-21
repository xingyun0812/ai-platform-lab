"""HITL Webhook 通知 — Phase H #40

使用 aiohttp 发送 HTTP POST 通知，带 HMAC-SHA256 签名和指数退避重试。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from packages.hitl.store import WebhookConfig

logger = logging.getLogger("ai_platform.hitl.webhook")


async def send_webhook(
    config: "WebhookConfig",
    payload: dict,
    timeout: float = 5.0,
) -> bool:
    """发送 webhook 通知，失败时重试最多 3 次（指数退避：1s, 2s, 4s）。

    Returns:
        True  — 至少一次 2xx 响应
        False — 全部失败
    """
    if not config.enabled or not config.url:
        return False

    try:
        import aiohttp  # noqa: PLC0415
    except ImportError:
        logger.warning("aiohttp 未安装，跳过 webhook 发送")
        return False

    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = _compute_signature(config.secret, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-Hitl-Signature": sig,
        **config.headers,
    }

    delays = [1.0, 2.0, 4.0]
    for attempt, delay in enumerate(delays):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.url,
                    data=body_bytes,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if 200 <= resp.status < 300:
                        logger.debug(
                            "webhook 发送成功 attempt=%d status=%d",
                            attempt + 1, resp.status,
                        )
                        return True
                    logger.warning(
                        "webhook 非 2xx attempt=%d status=%d",
                        attempt + 1, resp.status,
                    )
        except Exception as exc:
            logger.warning("webhook 发送失败 attempt=%d: %s", attempt + 1, exc)

        if attempt < len(delays) - 1:
            await asyncio.sleep(delay)

    return False


def _compute_signature(secret: str, body: bytes) -> str:
    """计算 HMAC-SHA256 签名（hex 编码）。"""
    key = secret.encode("utf-8") if secret else b""
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """验证 X-Hitl-Signature 签名。"""
    expected = _compute_signature(secret, body)
    return hmac.compare_digest(expected, signature)
