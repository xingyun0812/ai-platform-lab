"""语义缓存 — Phase F/G #34

在 Gateway 层拦截 /v1/chat/completions：
- exact 模式：SHA256(messages) 精确匹配（无 embedding 也可用）
- semantic 模式：对最后一条 user message 做 embedding，cosine 相似度 ≥ 阈值即命中

存储：
- InMemorySemanticCache（默认，进程内）
- RedisSemanticCache（REDIS_URL 可达时自动启用，跨实例共享）

约束：
- 跳过 stream=true
- 跳过 temperature 过高（默认 > 0.3）的请求
- 跳过 skip_cache_models 列表中的模型
- 仅缓存 2xx 响应
- per-tenant 隔离
"""

from packages.semantic_cache.metrics import (
    SemanticCacheMetrics,
    get_semantic_cache_metrics,
)
from packages.semantic_cache.store import (
    CacheEntry,
    CacheLookupResult,
    InMemorySemanticCache,
    RedisSemanticCache,
    SemanticCache,
    SemanticCacheConfig,
    build_cache_key,
    cosine_similarity,
    get_semantic_cache,
    init_semantic_cache,
    normalize_messages,
)

__all__ = [
    "CacheEntry",
    "CacheLookupResult",
    "InMemorySemanticCache",
    "RedisSemanticCache",
    "SemanticCache",
    "SemanticCacheConfig",
    "SemanticCacheMetrics",
    "build_cache_key",
    "cosine_similarity",
    "get_semantic_cache",
    "get_semantic_cache_metrics",
    "init_semantic_cache",
    "normalize_messages",
]
