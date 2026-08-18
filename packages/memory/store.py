"""长记忆存储 — Postgres 主存 + Redis 热缓存 + 进程内兜底。

MemoryRecord：
    memory_id, tenant_id, scope, scope_id, content, summary, embedding, metadata, created_at, expires_at

scope 三级：
    session — 单会话短期（自动 TTL）
    user    — 跨会话中期（用户级长期偏好/历史）
    tenant  — 租户级共享知识（团队级）

检索模式：
    keyword — content LIKE '%query%' 模糊匹配（默认，无依赖）
    semantic — embedding cosine similarity（需 embedding 服务）
"""

from __future__ import annotations

import dataclasses as _dc
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.memory.config import MemoryGovernanceConfig
from packages.memory.metrics import get_memory_metrics

logger = logging.getLogger("ai_platform.memory")


_CHINESE_RANGE = range(0x4E00, 0xA000)  # CJK Unified Ideographs


def _has_letter(content: str) -> bool:
    """Check if content has at least one letter (including CJK)."""
    for ch in content:
        if ch.isalpha():
            return True
        if ord(ch) in _CHINESE_RANGE:
            return True
        # Also check other Unicode letter categories
        cat = ord(ch)
        if 0x3400 <= cat < 0x4DC0:  # CJK Extension A + B
            return True
    return False


@dataclass
class MemoryRecord:
    memory_id: str
    tenant_id: str
    scope: str  # session | user | tenant
    scope_id: str
    content: str
    summary: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    access_count: int = 0
    last_accessed_at: float | None = None
    weight: float = 1.0
    merged_from: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _dc.asdict(self)

    def is_expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.time()) > self.expires_at


def _gen_id() -> str:
    return f"mem-{uuid.uuid4().hex[:16]}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


def _apply_weighted_score(
    scored: list[tuple[float, MemoryRecord]],
    *,
    records: list[MemoryRecord] | None = None,
    config: MemoryGovernanceConfig | None = None,
) -> list[tuple[float, MemoryRecord]]:
    """Convert raw similarity scores to weighted final scores.

    Uses the L5 weighted scoring formula:
        final_score = similarity * 0.7 + computed_weight * 0.3
    """
    from packages.memory.governance.weight import ScopeStats as _SS
    from packages.memory.governance.weight import compute_scope_stats as _css
    from packages.memory.governance.weight import compute_weight

    if not scored:
        return scored
    cfg = config or MemoryGovernanceConfig()
    scope_stats = _css(records) if records else _SS()
    result: list[tuple[float, MemoryRecord]] = []
    for sim, r in scored:
        cw = compute_weight(r, scope_stats, cfg)
        final_score = sim * 0.7 + cw * 0.3
        result.append((final_score, r))
    return result


# --------------------------------------------------------------------- #
# Quality filter
# --------------------------------------------------------------------- #


def quality_filter(
    record: MemoryRecord,
    *,
    config: MemoryGovernanceConfig | None = None,
    input_message: str | None = None,
) -> tuple[bool, str]:
    """L1 准入过滤：拦截低质数据。

    Args:
        record: 待写入的记忆记录。
        config: 治理配置。为 None 时使用默认配置。
        input_message: 触发该记忆的输入消息（用于回声检测）。

    Returns:
        (pass: bool, reason: str) — (True, "") 表示放行；
        (False, reason) 表示拦截。
    """
    cfg = config or MemoryGovernanceConfig()

    if not cfg.quality_filter_enabled:
        return True, ""

    content = record.content or ""

    # min_content_length
    if len(content) < cfg.min_content_length:
        return False, f"content too short ({len(content)} < {cfg.min_content_length})"

    # has_substance: not just punctuation/whitespace/numbers
    if not _has_letter(content):
        return False, "content has no substance (only punctuation/whitespace/numbers)"

    # not_duplicate_of_input: echo guard
    if input_message is not None and content.strip() == input_message.strip():
        return False, "content is identical to input message (echo guard)"

    return True, ""


class MemoryStore:
    """长记忆存储基类。"""

    def __init__(
        self,
        *,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> None:
        self._metrics = get_memory_metrics()
        self._governance_config = governance_config or MemoryGovernanceConfig()

    async def add(self, record: MemoryRecord) -> str:
        raise NotImplementedError

    async def get(self, memory_id: str) -> MemoryRecord | None:
        raise NotImplementedError

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    async def delete(self, memory_id: str) -> bool:
        raise NotImplementedError

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    async def count_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
    ) -> int:
        raise NotImplementedError

    async def list_expired(
        self,
        *,
        tenant_id: str = "*",
        scope: str = "*",
        scope_id: str = "*",
    ) -> list[MemoryRecord]:
        raise NotImplementedError


# --------------------------------------------------------------------- #
# 进程内实现
# --------------------------------------------------------------------- #


class InMemoryMemoryStore(MemoryStore):
    def __init__(self, *, governance_config: MemoryGovernanceConfig | None = None) -> None:
        super().__init__(governance_config=governance_config)
        self._lock = threading.RLock()
        # _records[(tenant_id, scope, scope_id)] = list[MemoryRecord]
        self._records: dict[tuple[str, str, str], list[MemoryRecord]] = {}
        self._by_id: dict[str, MemoryRecord] = {}

    async def add(
        self,
        record: MemoryRecord,
        *,
        input_message: str | None = None,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> str:
        passed, reason = quality_filter(
            record,
            config=governance_config or self._governance_config,
            input_message=input_message,
        )
        if not passed:
            logger.warning(
                "quality_filter rejected memory %s: %s",
                record.memory_id,
                reason,
            )
            self._metrics.record_quality_rejected(tenant_id=record.tenant_id, scope=record.scope)
            return record.memory_id  # still return the id, but don't store

        cfg = governance_config or self._governance_config

        # L2: Semantic dedup check
        from packages.memory.governance.dedup import check_dedup

        with self._lock:
            key = (record.tenant_id, record.scope, record.scope_id)
            bucket = self._records.get(key, [])
            # Sort by last_accessed_at desc, take top N candidates
            sorted_candidates = sorted(
                bucket,
                key=lambda r: r.last_accessed_at or 0,
                reverse=True,
            )[: cfg.dedup_candidate_count]

        dedup_result = check_dedup(record, sorted_candidates, cfg)

        if dedup_result.action == "skip":
            logger.warning(
                "dedup skipped memory %s (matched %s): %s",
                record.memory_id,
                dedup_result.matched_id,
                dedup_result.reason,
            )
            self._metrics.record_dedup_skipped(tenant_id=record.tenant_id, scope=record.scope)
            return dedup_result.matched_id or record.memory_id

        if dedup_result.action == "merge":
            logger.warning(
                "dedup merging memory %s into %s: %s",
                record.memory_id,
                dedup_result.matched_id,
                dedup_result.reason,
            )
            # Perform the merge on the matched record
            with self._lock:
                matched = self._by_id.get(dedup_result.matched_id or "")
                if matched is not None:
                    # Simple content merge (append unique content)
                    if record.content not in matched.content:
                        matched.content = matched.content + "\n" + record.content
                    matched.last_accessed_at = time.time()
                    matched.access_count = (matched.access_count or 0) + 1
                    if not hasattr(matched, "merged_from") or matched.merged_from is None:
                        matched.merged_from = []
                    if record.memory_id not in matched.merged_from:
                        matched.merged_from.append(record.memory_id)
                    # Merge metadata
                    if record.metadata:
                        matched.metadata.update(record.metadata)
            self._metrics.record_dedup_merged(tenant_id=record.tenant_id, scope=record.scope)
            return dedup_result.matched_id or record.memory_id

        # action == "insert" — normal insert
        with self._lock:
            key = (record.tenant_id, record.scope, record.scope_id)
            self._records.setdefault(key, []).append(record)
            self._by_id[record.memory_id] = record
        self._metrics.record_add(tenant_id=record.tenant_id, scope=record.scope)
        return record.memory_id

    async def get(self, memory_id: str) -> MemoryRecord | None:
        """Get a memory record and auto-update access tracking."""
        with self._lock:
            r = self._by_id.get(memory_id)
            if r is None:
                return None
            if r.is_expired():
                return None
            r.access_count += 1
            r.last_accessed_at = time.time()
            return r

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        import time as _time

        start = _time.perf_counter()
        with self._lock:
            key = (tenant_id, scope, scope_id)
            records = [r for r in self._records.get(key, []) if not r.is_expired()]
        # 评分
        scored: list[tuple[float, MemoryRecord]] = []
        q_lower = query.lower()
        for r in records:
            if query_embedding is not None and r.embedding is not None:
                sim = _cosine_similarity(query_embedding, r.embedding)
                scored.append((sim, r))
            else:
                # keyword 模糊匹配：简单子串命中数
                content_lower = r.content.lower()
                if q_lower in content_lower:
                    score = 1.0
                else:
                    # 分词命中数
                    q_tokens = [t for t in q_lower.split() if t]
                    if not q_tokens:
                        score = 0.0
                    else:
                        hits = sum(1 for t in q_tokens if t in content_lower)
                        score = hits / len(q_tokens)
                scored.append((score, r))
        # Apply weighted scoring before sorting (semantic search only)
        if query_embedding is not None:
            scored = _apply_weighted_score(
                scored,
                records=records,
                config=self._governance_config,
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _s, r in scored[:top_k] if _s > 0]
        # Auto-update access tracking for matched records
        now = _time.time()
        for r in results:
            r.access_count += 1
            r.last_accessed_at = now
        latency_ms = (_time.perf_counter() - start) * 1000
        self._metrics.record_search(tenant_id=tenant_id, scope=scope)
        self._metrics.record_search_latency(tenant_id=tenant_id, scope=scope, latency_ms=latency_ms)
        return results

    async def delete(self, memory_id: str) -> bool:
        with self._lock:
            r = self._by_id.pop(memory_id, None)
            if r is None:
                return False
            key = (r.tenant_id, r.scope, r.scope_id)
            bucket = self._records.get(key, [])
            self._records[key] = [x for x in bucket if x.memory_id != memory_id]
            return True

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        with self._lock:
            key = (tenant_id, scope, scope_id)
            records = [r for r in self._records.get(key, []) if not r.is_expired()]
        return records[:limit]

    async def count_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
    ) -> int:
        with self._lock:
            key = (tenant_id, scope, scope_id)
            return len([r for r in self._records.get(key, []) if not r.is_expired()])

    async def list_expired(
        self,
        *,
        tenant_id: str = "*",
        scope: str = "*",
        scope_id: str = "*",
    ) -> list[MemoryRecord]:
        with self._lock:
            results: list[MemoryRecord] = []
            for (tid, scp, sid), records in self._records.items():
                if tenant_id != "*" and tid != tenant_id:
                    continue
                if scope != "*" and scp != scope:
                    continue
                if scope_id != "*" and sid != scope_id:
                    continue
                # Return all records as purge candidates (not just expired)
                results.extend(records)
            return results


# --------------------------------------------------------------------- #
# Postgres 实现
# --------------------------------------------------------------------- #


class PostgresMemoryStore(MemoryStore):
    """Postgres 持久化存储。

    Schema:
        CREATE TABLE agent_memories (
            memory_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            embedding JSONB,         -- [f1, f2, ...]
            metadata JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ
        );
        CREATE INDEX idx_mem_scope ON agent_memories (tenant_id, scope, scope_id);
        CREATE INDEX idx_mem_expires ON agent_memories (expires_at);
    """

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS agent_memories (
        memory_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        content TEXT NOT NULL,
        summary TEXT,
        embedding JSONB,
        metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ,
        access_count INTEGER DEFAULT 0,
        last_accessed_at DOUBLE PRECISION,
        weight DOUBLE PRECISION DEFAULT 1.0,
        merged_from JSONB DEFAULT '[]'::jsonb
    );
    CREATE INDEX IF NOT EXISTS idx_mem_scope
        ON agent_memories (tenant_id, scope, scope_id);
    """

    def __init__(
        self, database_url: str, *, governance_config: MemoryGovernanceConfig | None = None
    ) -> None:
        super().__init__(governance_config=governance_config)
        self._url = database_url
        self._init_schema()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _init_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(self.SCHEMA_SQL)
                conn.commit()
            logger.info("memory store schema initialized")
        except Exception as e:
            logger.error("memory store schema init failed: %s", e)
            raise

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
        emb = row.get("embedding")
        if isinstance(emb, str):
            try:
                emb = json.loads(emb)
            except Exception:
                emb = None
        meta = row.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if meta is None:
            meta = {}
        created_at_raw = row.get("created_at")
        created_at = (
            created_at_raw.timestamp()
            if hasattr(created_at_raw, "timestamp")
            else float(created_at_raw or time.time())
        )
        expires_raw = row.get("expires_at")
        expires_at = None
        if expires_raw is not None:
            expires_at = (
                expires_raw.timestamp() if hasattr(expires_raw, "timestamp") else float(expires_raw)
            )
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            tenant_id=str(row["tenant_id"]),
            scope=str(row["scope"]),
            scope_id=str(row["scope_id"]),
            content=str(row["content"]),
            summary=row.get("summary"),
            embedding=emb,
            metadata=meta if isinstance(meta, dict) else {},
            created_at=created_at,
            expires_at=expires_at,
            access_count=int(row.get("access_count", 0)),
            last_accessed_at=row.get("last_accessed_at"),
            weight=float(row.get("weight", 1.0)),
            merged_from=row.get("merged_from") or [],
        )

    async def add(
        self,
        record: MemoryRecord,
        *,
        input_message: str | None = None,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> str:
        passed, reason = quality_filter(
            record,
            config=governance_config or self._governance_config,
            input_message=input_message,
        )
        if not passed:
            logger.warning(
                "quality_filter rejected memory %s: %s",
                record.memory_id,
                reason,
            )
            self._metrics.record_quality_rejected(tenant_id=record.tenant_id, scope=record.scope)
            return record.memory_id

        cfg = governance_config or self._governance_config

        # L2: Semantic dedup check (fetch top N candidates from same scope)
        from packages.memory.governance.dedup import check_dedup

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY last_accessed_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (record.tenant_id, record.scope, record.scope_id, cfg.dedup_candidate_count),
                ).fetchall()
            candidates = [self._row_to_record(r) for r in rows]
        except Exception as e:
            logger.warning("dedup candidate fetch failed, skipping dedup: %s", e)
            candidates = []

        dedup_result = check_dedup(record, candidates, cfg)

        if dedup_result.action == "skip":
            logger.warning(
                "dedup skipped memory %s (matched %s): %s",
                record.memory_id,
                dedup_result.matched_id,
                dedup_result.reason,
            )
            self._metrics.record_dedup_skipped(tenant_id=record.tenant_id, scope=record.scope)
            return dedup_result.matched_id or record.memory_id

        if dedup_result.action == "merge":
            logger.warning(
                "dedup merging memory %s into %s: %s",
                record.memory_id,
                dedup_result.matched_id,
                dedup_result.reason,
            )
            matched_id = dedup_result.matched_id or ""
            try:
                with self._connect() as conn:
                    # Fetch the matched record
                    matched_row = conn.execute(
                        "SELECT * FROM agent_memories WHERE memory_id = %s",
                        (matched_id,),
                    ).fetchone()
                    if matched_row is not None:
                        existing_content = matched_row["content"]
                        existing_meta = matched_row.get("metadata", {})
                        if isinstance(existing_meta, str):
                            existing_meta = json.loads(existing_meta)
                        if existing_meta is None:
                            existing_meta = {}
                        existing_merged_from = matched_row.get("merged_from")
                        if isinstance(existing_merged_from, str):
                            existing_merged_from = json.loads(existing_merged_from)
                        if existing_merged_from is None:
                            existing_merged_from = []

                        # Merge content
                        if record.content not in existing_content:
                            new_content = existing_content + "\n" + record.content
                        else:
                            new_content = existing_content

                        # Merge metadata
                        if record.metadata:
                            existing_meta.update(record.metadata)

                        # Track merge history
                        if record.memory_id not in existing_merged_from:
                            existing_merged_from.append(record.memory_id)

                        conn.execute(
                            """
                            UPDATE agent_memories
                            SET content = %s,
                                metadata = %s,
                                last_accessed_at = %s,
                                access_count = access_count + 1,
                                merged_from = %s
                            WHERE memory_id = %s
                            """,
                            (
                                new_content,
                                json.dumps(existing_meta),
                                time.time(),
                                json.dumps(existing_merged_from),
                                matched_id,
                            ),
                        )
                        conn.commit()
            except Exception as e:
                logger.error("dedup merge failed for %s: %s", matched_id, e)
            self._metrics.record_dedup_merged(tenant_id=record.tenant_id, scope=record.scope)
            return matched_id

        # action == "insert" — normal insert
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_memories
                        (memory_id, tenant_id, scope, scope_id, content, summary,
                         embedding, metadata, created_at, expires_at,
                         access_count, last_accessed_at, weight)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.memory_id,
                        record.tenant_id,
                        record.scope,
                        record.scope_id,
                        record.content,
                        record.summary,
                        json.dumps(record.embedding) if record.embedding else None,
                        json.dumps(record.metadata),
                        record.created_at,
                        record.expires_at,
                        record.access_count,
                        record.last_accessed_at,
                        record.weight,
                    ),
                )
                conn.commit()
            self._metrics.record_add(tenant_id=record.tenant_id, scope=record.scope)
            return record.memory_id
        except Exception as e:
            logger.error("memory add failed: %s", e)
            self._metrics.record_store_error(tenant_id=record.tenant_id, scope=record.scope)
            raise

    async def get(self, memory_id: str) -> MemoryRecord | None:
        """Get a memory record and auto-update access tracking."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM agent_memories WHERE memory_id = %s",
                    (memory_id,),
                ).fetchone()
            if row is None:
                return None
            r = self._row_to_record(row)
            if r.is_expired():
                return None
            # Auto-update access tracking in DB
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE agent_memories
                    SET access_count = access_count + 1,
                        last_accessed_at = %s
                    WHERE memory_id = %s
                    """,
                    (time.time(), memory_id),
                )
                conn.commit()
            r.access_count += 1
            r.last_accessed_at = time.time()
            return r
        except Exception as e:
            logger.error("memory get failed: %s", e)
            return None

    async def search(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[MemoryRecord]:
        import time as _time

        start = _time.perf_counter()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, scope, scope_id, max(top_k * 4, 20)),
                ).fetchall()
        except Exception as e:
            logger.error("memory search failed: %s", e)
            self._metrics.record_store_error(tenant_id=tenant_id, scope=scope)
            return []
        records = [self._row_to_record(r) for r in rows]
        # 内存中打分（与 InMemory 一致）
        scored: list[tuple[float, MemoryRecord]] = []
        q_lower = query.lower()
        for r in records:
            if query_embedding is not None and r.embedding is not None:
                sim = _cosine_similarity(query_embedding, r.embedding)
                scored.append((sim, r))
            else:
                content_lower = r.content.lower()
                if q_lower in content_lower:
                    score = 1.0
                else:
                    q_tokens = [t for t in q_lower.split() if t]
                    if not q_tokens:
                        score = 0.0
                    else:
                        hits = sum(1 for t in q_tokens if t in content_lower)
                        score = hits / len(q_tokens)
                scored.append((score, r))
        # Apply weighted scoring before sorting (semantic search only)
        if query_embedding is not None:
            scored = _apply_weighted_score(
                scored,
                records=records,
                config=self._governance_config,
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [r for _s, r in scored[:top_k] if _s > 0]
        # Auto-update access tracking for matched records
        if results:
            now = _time.time()
            matched_ids = [(now, r.memory_id) for r in results]
            try:
                with self._connect() as conn:
                    for ts, mid in matched_ids:
                        conn.execute(
                            """
                            UPDATE agent_memories
                            SET access_count = access_count + 1,
                                last_accessed_at = %s
                            WHERE memory_id = %s
                            """,
                            (ts, mid),
                        )
                    conn.commit()
            except Exception as e:
                logger.error("memory search access tracking failed: %s", e)
            for r in results:
                r.access_count += 1
                r.last_accessed_at = now
        latency_ms = (_time.perf_counter() - start) * 1000
        self._metrics.record_search(tenant_id=tenant_id, scope=scope)
        self._metrics.record_search_latency(tenant_id=tenant_id, scope=scope, latency_ms=latency_ms)
        return results

    async def delete(self, memory_id: str) -> bool:
        try:
            with self._connect() as conn:
                result = conn.execute(
                    "DELETE FROM agent_memories WHERE memory_id = %s",
                    (memory_id,),
                )
                conn.commit()
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error("memory delete failed: %s", e)
            return False

    async def list_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, scope, scope_id, limit),
                ).fetchall()
            return [self._row_to_record(r) for r in rows]
        except Exception as e:
            logger.error("memory list failed: %s", e)
            return []

    async def count_by_scope(
        self,
        *,
        tenant_id: str,
        scope: str,
        scope_id: str,
    ) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM agent_memories
                    WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                      AND (expires_at IS NULL OR expires_at > NOW())
                    """,
                    (tenant_id, scope, scope_id),
                ).fetchone()
            return int(row["cnt"]) if row else 0
        except Exception as e:
            logger.error("memory count failed: %s", e)
            return 0

    async def list_expired(
        self,
        *,
        tenant_id: str = "*",
        scope: str = "*",
        scope_id: str = "*",
    ) -> list[MemoryRecord]:
        try:
            with self._connect() as conn:
                # Return all records as purge candidates
                clauses: list[str] = []
                params: list[Any] = []
                if tenant_id != "*":
                    clauses.append("tenant_id = %s")
                    params.append(tenant_id)
                if scope != "*":
                    clauses.append("scope = %s")
                    params.append(scope)
                if scope_id != "*":
                    clauses.append("scope_id = %s")
                    params.append(scope_id)
                where = " AND ".join(clauses)
                sql = f"SELECT * FROM agent_memories WHERE {where} ORDER BY expires_at"
                rows = conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]
        except Exception as e:
            logger.error("memory list_expired failed: %s", e)
            return []


# --------------------------------------------------------------------- #
# 工厂与全局单例
# --------------------------------------------------------------------- #

_global_store: MemoryStore | None = None
_global_lock = threading.Lock()


def init_memory_store(
    *,
    database_url: str | None = None,
) -> MemoryStore:
    """初始化全局 MemoryStore。

    优先级：
    1. DATABASE_URL 可达 → PostgresMemoryStore
    2. 否则 → InMemoryMemoryStore
    """
    global _global_store
    with _global_lock:
        if database_url:
            try:
                _global_store = PostgresMemoryStore(database_url)
                logger.info("memory store backend=postgres")
                return _global_store
            except Exception as e:
                logger.warning("postgres 不可达，回退进程内 memory store: %s", e)
        _global_store = InMemoryMemoryStore()
        logger.info("memory store backend=memory")
        return _global_store


def get_memory_store() -> MemoryStore | None:
    return _global_store


def reset_memory_store_for_tests() -> None:
    global _global_store
    with _global_lock:
        _global_store = None
