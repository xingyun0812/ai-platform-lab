"""语义缓存存储 — 支持 exact / semantic 两种命中策略。

存储后端：
- InMemorySemanticCache（默认，进程内 LRU + TTL）
- RedisSemanticCache（REDIS_URL 可达时自动启用，跨实例共享）

核心 API：
- lookup(tenant_id, model, messages, mode, similarity_threshold) -> CacheLookupResult | None
- store(tenant_id, model, messages, response, usage_tokens)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from packages.semantic_cache.metrics import get_semantic_cache_metrics

logger = logging.getLogger("ai_platform.semantic_cache")


# --------------------------------------------------------------------- #
# 消息归一化与 Key 生成
# --------------------------------------------------------------------- #

def normalize_messages(messages: list[dict[str, Any]]) -> str:
    """归一化 messages 列表用于缓存 key：
    - 去除空白
    - 拼接 role:content
    - 不含图片/工具调用（仅文本部分参与）
    """
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").strip()
        content = m.get("content")
        if isinstance(content, list):
            # OpenAI 多模态格式：抽取 text 段
            text_bits: list[str] = []
            for seg in content:
                if isinstance(seg, dict):
                    if seg.get("type") == "text" and seg.get("text"):
                        text_bits.append(str(seg["text"]).strip())
                elif isinstance(seg, str):
                    text_bits.append(seg.strip())
            content_str = " ".join(text_bits)
        else:
            content_str = str(content or "").strip()
        parts.append(f"{role}:{content_str}")
    return "\n".join(parts)


def build_cache_key(*, tenant_id: str, model: str, normalized: str) -> str:
    """exact 模式使用的确定性 key。"""
    h = hashlib.sha256()
    h.update(tenant_id.encode("utf-8"))
    h.update(b"|")
    h.update(model.encode("utf-8"))
    h.update(b"|")
    h.update(normalized.encode("utf-8"))
    return h.hexdigest()


def extract_query_text(normalized: str) -> str:
    """从归一化 messages 中提取最后一条 user 的 query。"""
    parts = normalized.split("\n")
    for p in reversed(parts):
        if p.startswith("user:"):
            return p[len("user:") :]
    return parts[-1] if parts else ""


# --------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------- #

@dataclass
class CacheEntry:
    """单个缓存条目。"""
    cache_key: str
    tenant_id: str
    model: str
    normalized: str
    response: dict[str, Any]
    embedding: list[float] | None = None
    usage_tokens: int = 0
    created_at: float = field(default_factory=time.time)


@dataclass
class CacheLookupResult:
    """缓存命中结果。"""
    entry: CacheEntry
    similarity: float
    mode: str  # "exact" | "semantic"


class SemanticCacheConfig:
    """语义缓存配置。"""

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: str = "semantic",  # "exact" | "semantic"
        similarity_threshold: float = 0.92,
        ttl_seconds: int = 3600,
        max_entries_per_tenant: int = 256,
        skip_models: list[str] | None = None,
        max_temperature: float = 0.3,
        embedding_dims: int = 1536,
    ) -> None:
        self.enabled = enabled
        self.mode = mode
        self.similarity_threshold = similarity_threshold
        self.ttl_seconds = ttl_seconds
        self.max_entries_per_tenant = max_entries_per_tenant
        self.skip_models = set(skip_models or [])
        self.max_temperature = max_temperature
        self.embedding_dims = embedding_dims


# --------------------------------------------------------------------- #
# 相似度计算
# --------------------------------------------------------------------- #

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 0.0:
        return 0.0
    return dot / denom


# --------------------------------------------------------------------- #
# 存储抽象与实现
# --------------------------------------------------------------------- #

class SemanticCache:
    """语义缓存基类。子类负责持久化（进程内 / Redis）。"""

    def __init__(self, config: SemanticCacheConfig) -> None:
        self.config = config
        self._metrics = get_semantic_cache_metrics()

    def _should_skip(
        self,
        *,
        tenant_id: str,
        model: str,
        temperature: float | None,
        stream: bool,
    ) -> str | None:
        """返回跳过原因；None 表示不跳过。"""
        if stream:
            return "stream=true"
        if model in self.config.skip_models:
            return f"model in skip_list: {model}"
        if temperature is not None and temperature > self.config.max_temperature:
            return f"temperature={temperature} > {self.config.max_temperature}"
        return None

    async def lookup(
        self,
        *,
        tenant_id: str,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float | None,
        stream: bool,
    ) -> CacheLookupResult | None | str:
        """返回：
        - CacheLookupResult：命中
        - None：未命中（应继续走上游）
        - str：跳过缓存（str 为原因；调用方应继续走上游且不写入）
        """
        skip_reason = self._should_skip(
            tenant_id=tenant_id, model=model, temperature=temperature, stream=stream
        )
        if skip_reason is not None:
            return skip_reason

        normalized = normalize_messages(messages)
        cache_key = build_cache_key(
            tenant_id=tenant_id, model=model, normalized=normalized
        )

        start = time.perf_counter()
        try:
            result = await self._lookup_impl(
                tenant_id=tenant_id,
                model=model,
                cache_key=cache_key,
                normalized=normalized,
            )
        except Exception as e:
            logger.warning("semantic cache lookup error: %s", e)
            self._metrics.record_store_error(tenant_id=tenant_id, model=model)
            return None
        latency_ms = (time.perf_counter() - start) * 1000
        self._metrics.record_lookup_latency(
            tenant_id=tenant_id, model=model, latency_ms=latency_ms
        )

        if result is not None:
            self._metrics.record_hit(tenant_id=tenant_id, model=model)
            self._metrics.record_tokens_saved(
                tenant_id=tenant_id,
                model=model,
                tokens=result.entry.usage_tokens,
            )
        else:
            self._metrics.record_miss(tenant_id=tenant_id, model=model)
        return result

    async def store(
        self,
        *,
        tenant_id: str,
        model: str,
        messages: list[dict[str, Any]],
        response: dict[str, Any],
        usage_tokens: int = 0,
        temperature: float | None,
        stream: bool,
    ) -> None:
        skip_reason = self._should_skip(
            tenant_id=tenant_id, model=model, temperature=temperature, stream=stream
        )
        if skip_reason is not None:
            return
        normalized = normalize_messages(messages)
        cache_key = build_cache_key(
            tenant_id=tenant_id, model=model, normalized=normalized
        )
        embedding: list[float] | None = None
        if self.config.mode == "semantic":
            embedding = await self._maybe_embed(normalized, tenant_id=tenant_id, model=model)
        entry = CacheEntry(
            cache_key=cache_key,
            tenant_id=tenant_id,
            model=model,
            normalized=normalized,
            response=response,
            embedding=embedding,
            usage_tokens=usage_tokens,
        )
        try:
            await self._store_impl(entry)
        except Exception as e:
            logger.warning("semantic cache store error: %s", e)
            self._metrics.record_store_error(tenant_id=tenant_id, model=model)

    # ---- 子类实现 ---- #

    async def _lookup_impl(
        self,
        *,
        tenant_id: str,
        model: str,
        cache_key: str,
        normalized: str,
    ) -> CacheLookupResult | None:
        raise NotImplementedError

    async def _store_impl(self, entry: CacheEntry) -> None:
        raise NotImplementedError

    # ---- Embedding 辅助 ---- #

    async def _maybe_embed(
        self, normalized: str, *, tenant_id: str, model: str
    ) -> list[float] | None:
        query = extract_query_text(normalized)
        if not query:
            return None
        try:
            from packages.rag.embeddings import embed_texts

            vectors = await embed_texts([query])
            if vectors and len(vectors[0]) > 0:
                return vectors[0]
        except Exception as e:
            logger.debug("embed for cache failed (fallback to exact match): %s", e)
            self._metrics.record_store_error(tenant_id=tenant_id, model=model)
        return None


# --------------------------------------------------------------------- #
# 进程内实现
# --------------------------------------------------------------------- #

class InMemorySemanticCache(SemanticCache):
    """进程内 LRU + TTL 缓存。按 tenant_id 分桶。"""

    def __init__(self, config: SemanticCacheConfig) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self._buckets: dict[str, OrderedDict[str, CacheEntry]] = {}

    def _purge_expired(self, bucket: "OrderedDict[str, CacheEntry]") -> None:
        now = time.time()
        expired = [
            k
            for k, e in list(bucket.items())
            if now - e.created_at > self.config.ttl_seconds
        ]
        for k in expired:
            bucket.pop(k, None)

    async def _lookup_impl(
        self,
        *,
        tenant_id: str,
        model: str,
        cache_key: str,
        normalized: str,
    ) -> CacheLookupResult | None:
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            if not bucket:
                return None
            self._purge_expired(bucket)
            # 1) exact match
            entry = bucket.get(cache_key)
            if entry is not None:
                # 刷新 LRU
                bucket.move_to_end(cache_key)
                return CacheLookupResult(entry=entry, similarity=1.0, mode="exact")
            # 2) semantic match（若启用）
            if self.config.mode == "semantic":
                query_emb = await self._maybe_embed(
                    normalized, tenant_id=tenant_id, model=model
                )
                if not query_emb:
                    return None
                best: CacheEntry | None = None
                best_sim = 0.0
                for e in list(bucket.values()):
                    if e.embedding is None:
                        continue
                    sim = cosine_similarity(query_emb, e.embedding)
                    if sim > best_sim:
                        best_sim = sim
                        best = e
                if best is not None and best_sim >= self.config.similarity_threshold:
                    return CacheLookupResult(entry=best, similarity=best_sim, mode="semantic")
            return None

    async def _store_impl(self, entry: CacheEntry) -> None:
        with self._lock:
            bucket = self._buckets.setdefault(entry.tenant_id, OrderedDict())
            self._purge_expired(bucket)
            if entry.cache_key in bucket:
                bucket.move_to_end(entry.cache_key)
            bucket[entry.cache_key] = entry
            # LRU 淘汰
            while len(bucket) > self.config.max_entries_per_tenant:
                bucket.popitem(last=False)


# --------------------------------------------------------------------- #
# Redis 实现
# --------------------------------------------------------------------- #

class RedisSemanticCache(SemanticCache):
    """Redis 跨实例语义缓存。

    数据结构：
    - exact:    Hash `ai_platform:sem_cache:{tenant_id}:exact` → cache_key → JSON(entry)
    - semantic: Hash `ai_platform:sem_cache:{tenant_id}:sem` → cache_key → JSON(entry with embedding)

    TTL 由 EXPIRE 控制；LRU 通过 sorted set 维护（简化：直接限制 size）。
    """

    KEY_PREFIX = "ai_platform:sem_cache"

    def __init__(self, config: SemanticCacheConfig, redis_client: Any) -> None:
        super().__init__(config)
        self._redis = redis_client

    def _exact_key(self, tenant_id: str) -> str:
        return f"{self.KEY_PREFIX}:{tenant_id}:exact"

    def _sem_key(self, tenant_id: str) -> str:
        return f"{self.KEY_PREFIX}:{tenant_id}:sem"

    async def _lookup_impl(
        self,
        *,
        tenant_id: str,
        model: str,
        cache_key: str,
        normalized: str,
    ) -> CacheLookupResult | None:
        exact_raw = await self._redis.hget(self._exact_key(tenant_id), cache_key)
        if exact_raw:
            try:
                data = json.loads(exact_raw)
                entry = self._entry_from_dict(data)
                if not self._is_expired(entry):
                    return CacheLookupResult(entry=entry, similarity=1.0, mode="exact")
            except Exception:
                pass

        if self.config.mode != "semantic":
            return None

        query_emb = await self._maybe_embed(
            normalized, tenant_id=tenant_id, model=model
        )
        if not query_emb:
            return None

        # 遍历所有 semantic 条目，找最相似
        all_entries = await self._redis.hgetall(self._sem_key(tenant_id))
        if not all_entries:
            return None
        best: CacheEntry | None = None
        best_sim = 0.0
        for k, v in all_entries.items():
            try:
                data = json.loads(v)
                entry = self._entry_from_dict(data)
                if self._is_expired(entry):
                    continue
                if not entry.embedding:
                    continue
                sim = cosine_similarity(query_emb, entry.embedding)
                if sim > best_sim:
                    best_sim = sim
                    best = entry
            except Exception:
                continue
        if best is not None and best_sim >= self.config.similarity_threshold:
            return CacheLookupResult(entry=best, similarity=best_sim, mode="semantic")
        return None

    async def _store_impl(self, entry: CacheEntry) -> None:
        data = self._entry_to_dict(entry)
        payload = json.dumps(data, ensure_ascii=False)
        if entry.embedding is not None and self.config.mode == "semantic":
            await self._redis.hset(self._sem_key(entry.tenant_id), entry.cache_key, payload)
            await self._redis.expire(self._sem_key(entry.tenant_id), self.config.ttl_seconds)
        # 同时存 exact（语义模式也写 exact，便于精确命中先返回）
        await self._redis.hset(self._exact_key(entry.tenant_id), entry.cache_key, payload)
        await self._redis.expire(self._exact_key(entry.tenant_id), self.config.ttl_seconds)

    def _entry_to_dict(self, entry: CacheEntry) -> dict[str, Any]:
        return {
            "cache_key": entry.cache_key,
            "tenant_id": entry.tenant_id,
            "model": entry.model,
            "normalized": entry.normalized,
            "response": entry.response,
            "embedding": entry.embedding,
            "usage_tokens": entry.usage_tokens,
            "created_at": entry.created_at,
        }

    def _entry_from_dict(self, data: dict[str, Any]) -> CacheEntry:
        return CacheEntry(
            cache_key=data["cache_key"],
            tenant_id=data["tenant_id"],
            model=data["model"],
            normalized=data["normalized"],
            response=data["response"],
            embedding=data.get("embedding"),
            usage_tokens=int(data.get("usage_tokens", 0)),
            created_at=float(data.get("created_at", time.time())),
        )

    def _is_expired(self, entry: CacheEntry) -> bool:
        return (time.time() - entry.created_at) > self.config.ttl_seconds


# --------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------- #

_global_cache: SemanticCache | None = None
_global_cache_lock = threading.Lock()


def init_semantic_cache(
    config: SemanticCacheConfig, *, redis_url: str | None = None
) -> SemanticCache:
    """初始化全局 SemanticCache。

    - redis_url 可达且非空 → RedisSemanticCache
    - 否则 → InMemorySemanticCache
    """
    global _global_cache
    with _global_cache_lock:
        if redis_url:
            try:
                from packages.state.redis_client import get_redis_client

                client = get_redis_client(redis_url)
                client.ping()
                _global_cache = RedisSemanticCache(config, client)
                logger.info("semantic cache backend=redis url=%s mode=%s", redis_url, config.mode)
                return _global_cache
            except Exception as e:
                logger.warning(
                    "redis 不可达，回退进程内 semantic cache: %s", e
                )
        _global_cache = InMemorySemanticCache(config)
        logger.info("semantic cache backend=memory mode=%s", config.mode)
        return _global_cache


def get_semantic_cache() -> SemanticCache | None:
    return _global_cache


def reset_semantic_cache_for_tests() -> None:
    global _global_cache
    with _global_cache_lock:
        _global_cache = None
