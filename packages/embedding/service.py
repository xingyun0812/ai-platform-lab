"""Embedding 服务 — Issue #35

EmbeddingService 提供：
- 统一 embed 接口（单次/批量）
- LRU 内存缓存（text sha256 → embedding）
- 缓存统计（hits / misses）
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_platform.embedding.service")

# 默认 LRU 最大条数
DEFAULT_CACHE_MAX_SIZE = 10000


class _LRUCache:
    """简单线程安全 LRU 缓存（OrderedDict 实现）。"""

    def __init__(self, maxsize: int = DEFAULT_CACHE_MAX_SIZE) -> None:
        self._maxsize = maxsize
        self._cache: OrderedDict = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> list | None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, value: list) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
            self._cache[key] = value

    def clear(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            return count

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (
                    self._hits / (self._hits + self._misses)
                    if (self._hits + self._misses) > 0
                    else 0.0
                ),
            }


class EmbeddingService:
    """Embedding 服务 — 统一 embed 接口 + LRU 缓存。"""

    def __init__(
        self,
        registry: Any,  # EmbeddingRegistry
        cache_max_size: int = DEFAULT_CACHE_MAX_SIZE,
    ) -> None:
        self._registry = registry
        self._cache = _LRUCache(maxsize=cache_max_size)
        self._lock = threading.RLock()

    async def embed(self, request: Any) -> Any:
        """生成 embedding。

        Args:
            request: EmbeddingRequest

        Returns:
            EmbeddingResponse
        """
        # 延迟导入避免循环依赖
        from packages.embedding.models import EmbeddingResponse
        from packages.embedding.providers import provider_factory

        model = self._registry.get_model(request.model_id)
        if model is None:
            raise ValueError(f"embedding model {request.model_id!r} not found in registry")

        provider = provider_factory(model)

        embeddings = []
        all_cached = True
        cache_hits = 0
        texts_to_embed = []
        text_indices = []

        # 检查缓存
        cached_results: dict[int, list] = {}
        for i, text in enumerate(request.texts):
            cache_key = self._cache_key(request.model_id, text)
            cached = self._cache.get(cache_key)
            if cached is not None:
                cached_results[i] = cached
                cache_hits += 1
            else:
                all_cached = False
                texts_to_embed.append(text)
                text_indices.append(i)

        # 批量调用 provider（仅未命中缓存的文本）
        if texts_to_embed:
            new_embeddings = await provider.embed(texts_to_embed, model)
            for idx, (text, embedding) in enumerate(zip(texts_to_embed, new_embeddings)):
                orig_idx = text_indices[idx]
                cached_results[orig_idx] = embedding
                cache_key = self._cache_key(request.model_id, text)
                self._cache.set(cache_key, embedding)

        # 按原顺序组装结果
        for i in range(len(request.texts)):
            embeddings.append(cached_results[i])

        total = len(request.texts)
        return EmbeddingResponse(
            model_id=request.model_id,
            embeddings=embeddings,
            dimensions=model.dimensions,
            usage={
                "total_texts": total,
                "cached_texts": cache_hits,
                "computed_texts": total - cache_hits,
            },
            cached=(cache_hits > 0 and all_cached),
        )

    async def embed_one(
        self,
        model_id: str,
        text: str,
        tenant_id: str = "system",
    ) -> list:
        """便捷方法：单文本 embedding。"""
        from packages.embedding.models import EmbeddingRequest

        req = EmbeddingRequest(model_id=model_id, texts=[text], tenant_id=tenant_id)
        resp = await self.embed(req)
        return resp.embeddings[0]

    def cache_stats(self) -> dict[str, Any]:
        """返回缓存统计。"""
        return self._cache.stats()

    def clear_cache(self) -> int:
        """清除缓存，返回清除条数。"""
        return self._cache.clear()

    @staticmethod
    def _cache_key(model_id: str, text: str) -> str:
        """生成缓存键（model_id + text sha256）。"""
        digest = hashlib.sha256(f"{model_id}:{text}".encode("utf-8")).hexdigest()
        return digest


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_service: EmbeddingService | None = None
_global_lock = threading.Lock()


def init_embedding_service(
    *,
    registry_yaml_path: Path | None = None,
    registry_overrides_path: Path | None = None,
    cache_max_size: int = DEFAULT_CACHE_MAX_SIZE,
) -> EmbeddingService:
    """初始化全局 EmbeddingService 单例。"""
    global _global_service
    from packages.embedding.models import init_registry

    with _global_lock:
        registry = init_registry(
            yaml_path=registry_yaml_path,
            overrides_path=registry_overrides_path,
        )
        _global_service = EmbeddingService(
            registry=registry,
            cache_max_size=cache_max_size,
        )
        logger.info(
            "embedding service initialized models=%d cache_max=%d",
            len(registry.list_models()),
            cache_max_size,
        )
        return _global_service


def get_embedding_service() -> EmbeddingService | None:
    """获取全局 EmbeddingService 单例。"""
    return _global_service


def reset_embedding_service_for_tests() -> None:
    """重置全局单例（仅用于测试）。"""
    global _global_service
    from packages.embedding.models import reset_for_tests as reset_registry

    with _global_lock:
        _global_service = None
    reset_registry()
