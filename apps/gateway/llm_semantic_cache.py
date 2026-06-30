"""Chat/RAG 共享 semantic cache 辅助（Issue #182 / 架构 §9）。"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.semantic_cache import get_semantic_cache

logger = logging.getLogger("ai_platform.gateway.llm_semantic_cache")


async def lookup_llm_completion(
    *,
    tenant_id: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None,
) -> dict[str, Any] | None:
    """查 semantic cache；命中返回 upstream completion body，否则 None。"""
    cache = get_semantic_cache()
    if cache is None:
        return None
    cache_lookup = await cache.lookup(
        tenant_id=tenant_id,
        model=model,
        messages=messages,
        temperature=temperature,
        stream=False,
    )
    if isinstance(cache_lookup, str):
        logger.debug("semantic cache skipped: %s", cache_lookup)
        return None
    if cache_lookup is None:
        return None
    body = dict(cache_lookup.entry.response)
    meta = body.setdefault("_platform", {})
    if isinstance(meta, dict):
        meta["cache_hit"] = True
        meta["cache_mode"] = cache_lookup.mode
        meta["cache_similarity"] = round(cache_lookup.similarity, 4)
        meta["cache_age_seconds"] = round(time.time() - cache_lookup.entry.created_at, 2)
        meta["model"] = cache_lookup.entry.model
        meta["tenant_id"] = tenant_id
    logger.info(
        "semantic cache hit tenant=%s model=%s mode=%s sim=%.4f",
        tenant_id,
        model,
        cache_lookup.mode,
        cache_lookup.similarity,
    )
    return body


async def store_llm_completion(
    *,
    tenant_id: str,
    model: str,
    messages: list[dict[str, Any]],
    response: dict[str, Any],
    usage_tokens: int,
    temperature: float | None,
) -> None:
    cache = get_semantic_cache()
    if cache is None:
        return
    try:
        await cache.store(
            tenant_id=tenant_id,
            model=model,
            messages=messages,
            response=response,
            usage_tokens=usage_tokens,
            temperature=temperature,
            stream=False,
        )
    except Exception:
        logger.exception("semantic cache store failed")
