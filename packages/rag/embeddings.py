from __future__ import annotations

import logging
from typing import Any

import httpx

from apps.gateway.settings import get_settings

logger = logging.getLogger("ai_platform.rag.embeddings")


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    直连上游 OpenAI 兼容 /embeddings（与 gateway 共用 LLM_BASE_URL / LLM_API_KEY）。
    """
    if not texts:
        return []

    settings = get_settings()
    key = (settings.llm_api_key or "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY 未配置，无法生成 embedding")

    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/embeddings"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": settings.embedding_model,
        "input": texts,
    }
    timeout = httpx.Timeout(settings.upstream_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers=headers)
        if not r.is_success:
            raise RuntimeError(f"embeddings upstream {r.status_code}: {r.text[:500]}")
        body = r.json()
        data = body.get("data")
        if not isinstance(data, list):
            raise RuntimeError("embeddings 响应缺少 data 列表")
        # 按 index 排序，保证与输入顺序一致
        rows = sorted(data, key=lambda x: x.get("index", 0))
        vectors: list[list[float]] = []
        for row in rows:
            emb = row.get("embedding")
            if not isinstance(emb, list):
                raise RuntimeError("embedding 项格式错误")
            vectors.append([float(x) for x in emb])
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding 数量不匹配: {len(vectors)} != {len(texts)}")
        return vectors
