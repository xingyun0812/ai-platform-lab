"""Embedding 提供商抽象 — Issue #35

提供商：
- StubProvider  — 确定性哈希向量（测试用，无 LLM 调用）
- OpenAIProvider — OpenAI Embeddings API
- provider_factory — 根据模型配置选择提供商
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger("ai_platform.embedding.providers")


class EmbeddingProvider:
    """Embedding 提供商抽象基类。"""

    async def embed(self, items: list, model: Any) -> list:
        """为输入列表生成 embedding（每项为 canonical dict 或 str）。"""
        raise NotImplementedError


class StubProvider(EmbeddingProvider):
    """确定性 stub 提供商 — 基于 MD5 哈希生成固定维度向量。

    特性：
    - 无 LLM 调用，适合测试和 CI
    - 同一输入始终返回相同向量（确定性）
    - 支持 text / image_url / image_base64
    """

    async def embed(self, items: list, model: Any) -> list:
        from packages.embedding.multimodal import item_fingerprint, normalize_item

        result = []
        for raw in items:
            if isinstance(raw, dict):
                fp = item_fingerprint(raw)
            else:
                fp = item_fingerprint(normalize_item(raw))
            vec = self._hash_to_vector(fp, model.dimensions)
            result.append(vec)
        return result

    def _hash_to_vector(self, text: str, dimensions: int) -> list:
        """将文本 MD5 哈希扩展为指定维度的浮点向量。

        使用 MD5 digest 的每个字节归一化为 [-1, 1] 范围内的确定性浮点数，
        避免 float32 解包可能产生的 NaN / Inf 值。
        """
        seed = text.encode("utf-8")
        floats = []
        i = 0
        while len(floats) < dimensions:
            digest = hashlib.md5(seed + str(i).encode()).digest()
            # 每个字节 → [-1, 1] 范围的 float（确定性且无溢出）
            for byte in digest:
                floats.append((byte - 127.5) / 127.5)
            i += 1
        floats = floats[:dimensions]
        # L2 归一化
        norm = sum(x * x for x in floats) ** 0.5
        if norm > 0:
            floats = [x / norm for x in floats]
        return floats


class OpenAIProvider(EmbeddingProvider):
    """OpenAI Embeddings API 提供商。

    支持模型：
    - text-embedding-3-small  (1536 维)
    - text-embedding-3-large  (3072 维)
    - text-embedding-ada-002  (1536 维)
    """

    SUPPORTED_MODELS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def embed(self, items: list, model: Any) -> list:
        """调用 OpenAI Embeddings API（多模态输入降级为 text / content）。"""
        import httpx

        from packages.embedding.multimodal import items_to_openai_input, normalize_item

        canonical = [normalize_item(i) if not isinstance(i, dict) else i for i in items]
        payload_inputs = [items_to_openai_input([item]) for item in canonical]

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        results: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for inp in payload_inputs:
                body = {"model": model.model_id, "input": inp}
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                items_sorted = sorted(data["data"], key=lambda x: x["index"])
                results.append(items_sorted[0]["embedding"])
        return results


def provider_factory(model: Any) -> EmbeddingProvider:
    """根据模型配置返回合适的提供商。

    决策逻辑：
    1. provider == "stub" → StubProvider
    2. 未配置 LLM_API_KEY → StubProvider（降级）
    3. provider == "openai" → OpenAIProvider
    4. 其他 → StubProvider（未知提供商降级）
    """
    if model.provider == "stub":
        return StubProvider()

    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "embedding provider=%s but LLM_API_KEY not set, falling back to StubProvider",
            model.provider,
        )
        return StubProvider()

    if model.provider == "openai":
        base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        return OpenAIProvider(api_key=api_key, base_url=base_url)

    logger.warning(
        "unknown embedding provider=%s, falling back to StubProvider",
        model.provider,
    )
    return StubProvider()
