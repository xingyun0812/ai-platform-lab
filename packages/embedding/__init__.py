"""Embedding 独立服务包 — Issue #35

提供：
- EmbeddingProvider — 抽象基类
- StubProvider — 确定性哈希向量（测试/降级）
- OpenAIProvider — OpenAI Embeddings API
- EmbeddingModel — 模型配置数据类
- EmbeddingRequest / EmbeddingResponse — 请求/响应数据类
- EmbeddingRegistry — 模型注册表（YAML + JSON overrides）
- EmbeddingService — 服务（embed + LRU 缓存）
- init_embedding_service / get_embedding_service / reset_embedding_service_for_tests — 全局单例
"""

from __future__ import annotations

from packages.embedding.models import (
    EmbeddingModel,
    EmbeddingRegistry,
    EmbeddingRequest,
    EmbeddingResponse,
    get_registry,
    init_registry,
    reset_for_tests,
)
from packages.embedding.providers import (
    EmbeddingProvider,
    OpenAIProvider,
    StubProvider,
    provider_factory,
)
from packages.embedding.service import (
    EmbeddingService,
    get_embedding_service,
    init_embedding_service,
    reset_embedding_service_for_tests,
)

__all__ = [
    "EmbeddingModel",
    "EmbeddingProvider",
    "EmbeddingRegistry",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "EmbeddingService",
    "OpenAIProvider",
    "StubProvider",
    "get_embedding_service",
    "get_registry",
    "init_embedding_service",
    "init_registry",
    "provider_factory",
    "reset_embedding_service_for_tests",
    "reset_for_tests",
]
