"""RAG chunk embedding（文本 + 多模态图片）。"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.settings import get_settings
from packages.rag.chunker import TextChunk
from packages.rag.multimodal_index import guess_image_mime

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


async def _embed_via_service(
    *,
    model_id: str,
    inputs: list[dict[str, Any]],
) -> list[list[float]]:
    from packages.embedding.models import EmbeddingRequest
    from packages.embedding.service import get_embedding_service

    svc = get_embedding_service()
    if svc is None:
        raise RuntimeError("Embedding 服务未初始化")
    req = EmbeddingRequest(model_id=model_id, inputs=inputs)
    resp = await svc.embed(req)
    return resp.embeddings


async def embed_image_chunk(chunk: TextChunk) -> list[float]:
    """为图片 chunk 生成向量（优先 Embedding 服务多模态）。"""
    settings = get_settings()
    path = resolve_source_path(chunk.source_uri)
    raw = path.read_bytes()
    mime = guess_image_mime(path)
    b64 = base64.b64encode(raw).decode("ascii")

    if settings.embedding_service_enabled:
        model_id = settings.rag_multimodal_embedding_model
        vectors = await _embed_via_service(
            model_id=model_id,
            inputs=[{"type": "image_base64", "mime": mime, "data": b64}],
        )
        return vectors[0]

    # 降级：仅 embed caption 文本
    logger.warning(
        "rag image chunk fallback to caption text embed source=%s",
        chunk.source_uri,
    )
    return (await embed_texts([chunk.text]))[0]


async def embed_rag_chunks(chunks: list[TextChunk]) -> list[list[float]]:
    """按 chunk modality 批量/逐条 embed。"""
    if not chunks:
        return []

    text_chunks = [c for c in chunks if c.modality != "image"]
    image_chunks = [c for c in chunks if c.modality == "image"]

    vectors_by_id: dict[str, list[float]] = {}

    if text_chunks:
        texts = [c.text for c in text_chunks]
        text_vectors = await embed_texts(texts)
        for chunk, vec in zip(text_chunks, text_vectors, strict=True):
            vectors_by_id[chunk.chunk_id] = vec

    for chunk in image_chunks:
        vectors_by_id[chunk.chunk_id] = await embed_image_chunk(chunk)

    return [vectors_by_id[c.chunk_id] for c in chunks]
