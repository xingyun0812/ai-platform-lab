"""packages/agent/experience_store.py — Phase R R1 Agent 经验库。

经验库 — 三层 backend：
1. InMemoryExperienceStore（默认，无依赖）
2. PostgresExperienceStore（DATABASE_URL 可达时）
3. embedding 语义检索（EmbeddingService 可用时）

降级链：
- embedding 服务不可用 → hash 精确匹配
- Postgres 不可达 → 内存 store
- 任何步骤失败不阻塞主流程
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import AgentPlan
from packages.memory.config import MemoryGovernanceConfig

logger = logging.getLogger("ai_platform.agent.experience_store")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class ExperienceMetrics:
    """ExperienceStore 治理指标。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quality_rejected: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._dedup_skipped: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._dedup_merged: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._stores: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._retrieves: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._store_errors: defaultdict[tuple[str, str], int] = defaultdict(int)

    def record_quality_rejected(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._quality_rejected[key] += 1

    def record_dedup_skipped(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._dedup_skipped[key] += 1

    def record_dedup_merged(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._dedup_merged[key] += 1

    def record_store(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._stores[key] += 1

    def record_retrieve(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._retrieves[key] += 1

    def record_store_error(self, *, tenant_id: str) -> None:
        key = (tenant_id or "unknown", "experience")
        with self._lock:
            self._store_errors[key] += 1

    def prometheus_text(self) -> str:
        with self._lock:
            quality_rejected = dict(self._quality_rejected)
            dedup_skipped = dict(self._dedup_skipped)
            dedup_merged = dict(self._dedup_merged)
            stores = dict(self._stores)
            retrieves = dict(self._retrieves)
            store_errors = dict(self._store_errors)
        lines: list[str] = []
        for label, data in [
            ("experience_quality_rejected_total", quality_rejected),
            ("experience_dedup_skipped_total", dedup_skipped),
            ("experience_dedup_merged_total", dedup_merged),
            ("experience_stores_total", stores),
            ("experience_retrieves_total", retrieves),
            ("experience_store_errors_total", store_errors),
        ]:
            lines.append(f"# HELP {label} ExperienceStore counter")
            lines.append(f"# TYPE {label} counter")
            for (t, s), c in sorted(data.items()):
                lines.append(f'{label}{{tenant_id="{t}",scope="{s}"}} {c}')
        return "\n".join(lines) + "\n"


_experience_metrics: ExperienceMetrics | None = None
_experience_metrics_lock = threading.Lock()


def get_experience_metrics() -> ExperienceMetrics:
    global _experience_metrics
    if _experience_metrics is None:
        with _experience_metrics_lock:
            if _experience_metrics is None:
                _experience_metrics = ExperienceMetrics()
    return _experience_metrics


def reset_experience_metrics_for_tests() -> None:
    global _experience_metrics
    with _experience_metrics_lock:
        _experience_metrics = None


# ---------------------------------------------------------------------------
# CJK letter detection
# ---------------------------------------------------------------------------


_CHINESE_RANGE = range(0x4E00, 0xA000)  # CJK Unified Ideographs


def _has_letter(content: str) -> bool:
    """Check if content has at least one letter (including CJK)."""
    for ch in content:
        if ch.isalpha():
            return True
        if ord(ch) in _CHINESE_RANGE:
            return True
        cat = ord(ch)
        if 0x3400 <= cat < 0x4DC0:  # CJK Extension A + B
            return True
    return False


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ExperienceRecord:
    """一条 Agent 执行经验记录。"""

    experience_id: str
    tenant_id: str
    task_signature: str  # 任务签名（goal 的 SHA1 前 16 字符）
    goal: str
    plan: AgentPlan
    tool_calls: list[dict[str, Any]]
    outcome: str  # "success" | "partial" | "failed"
    lessons: str  # LLM 反思生成的 lessons
    created_at: float
    embedding: list[float] | None = None  # Phase R R1+: goal 的 embedding
    metadata: dict[str, Any] = field(default_factory=dict)
    access_count: int = 0
    last_accessed_at: float | None = None
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "tenant_id": self.tenant_id,
            "task_signature": self.task_signature,
            "goal": self.goal,
            "plan": self.plan.model_dump() if hasattr(self.plan, "model_dump") else self.plan,
            "tool_calls": self.tool_calls,
            "outcome": self.outcome,
            "lessons": self.lessons,
            "created_at": self.created_at,
            "embedding": self.embedding,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ExperienceRecord:
        """从 Postgres 行 dict 构造（plan_json / tool_calls_json 反序列化）。"""
        plan_data = row.get("plan_json")
        if isinstance(plan_data, str):
            plan_data = json.loads(plan_data)
        tool_calls_data = row.get("tool_calls_json")
        if isinstance(tool_calls_data, str):
            tool_calls_data = json.loads(tool_calls_data)
        embedding_data = row.get("embedding")
        if isinstance(embedding_data, str):
            embedding_data = json.loads(embedding_data)
        return cls(
            experience_id=row["experience_id"],
            tenant_id=row["tenant_id"],
            task_signature=row["task_signature"],
            goal=row["goal"],
            plan=AgentPlan.model_validate(plan_data)
            if not isinstance(plan_data, AgentPlan)
            else plan_data,
            tool_calls=tool_calls_data or [],
            outcome=row["outcome"],
            lessons=row["lessons"],
            created_at=float(row["created_at"]),
            embedding=embedding_data,
            metadata={},
            access_count=int(row.get("access_count", 0)),
            last_accessed_at=row.get("last_accessed_at"),
            weight=float(row.get("weight", 1.0)),
        )


# ---------------------------------------------------------------------------
# Governance filters
# ---------------------------------------------------------------------------


def quality_filter(
    record: ExperienceRecord,
    *,
    config: MemoryGovernanceConfig | None = None,
    input_message: str | None = None,
) -> tuple[bool, str]:
    """L1 准入过滤：拦截低质数据。

    Args:
        record: 待写入的经验记录。
        config: 治理配置。为 None 时使用默认配置。
        input_message: 触发该经验的输入消息（用于回声检测）。

    Returns:
        (pass: bool, reason: str) — (True, "") 表示放行；
        (False, reason) 表示拦截。
    """
    cfg = config or MemoryGovernanceConfig()

    if not cfg.quality_filter_enabled:
        return True, ""

    content = record.lessons or ""

    # min_content_length
    if len(content) < cfg.min_content_length:
        return False, f"content too short ({len(content)} < {cfg.min_content_length})"

    # has_substance: not just punctuation/whitespace/numbers
    if not _has_letter(content):
        return False, "content has no substance (only punctuation/whitespace/numbers)"

    # not_duplicate_of_input: echo guard
    if input_message is not None and content.strip() == input_message.strip():
        return False, "content is identical to input message (echo guard)"

    # also check against goal (experience-specific echo guard)
    if content.strip() == record.goal.strip():
        return False, "content is identical to goal (echo guard)"

    return True, ""


def dedup_filter(
    embedding: list[float] | None,
    existing_records: list[ExperienceRecord],
    *,
    config: MemoryGovernanceConfig | None = None,
) -> tuple[str, str | None]:
    """L2 语义去重过滤。

    Args:
        embedding: 新记录的 embedding 向量。None 时直接返回 ("store", None)。
        existing_records: 已有的经验记录列表（含 embedding）。
        config: 治理配置。为 None 时使用默认配置。

    Returns:
        (action: str, merged_id: str | None)
        action 为 "skip" / "merge_lessons" / "store" 之一。
        merged_id 在 merge_lessons 时指向被合并的已有记录 ID。
    """
    if embedding is None or not existing_records:
        return "store", None

    cfg = config or MemoryGovernanceConfig()
    max_sim = 0.0
    max_sim_id: str | None = None

    for r in existing_records:
        if r.embedding is None:
            continue
        sim = _cosine_similarity(embedding, r.embedding)
        if sim > max_sim:
            max_sim = sim
            max_sim_id = r.experience_id

    if max_sim_id is None:
        return "store", None

    if max_sim >= cfg.dedup_skip_threshold:
        return "skip", max_sim_id
    elif max_sim >= cfg.dedup_merge_threshold:
        return "merge_lessons", max_sim_id
    else:
        return "store", None


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class ExperienceStore:
    """经验库抽象基类。"""

    def __init__(
        self,
        *,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> None:
        self._metrics = get_experience_metrics()
        self._governance_config = governance_config or MemoryGovernanceConfig()

    async def store(self, record: ExperienceRecord) -> ExperienceRecord:
        raise NotImplementedError

    async def _store_inner(self, record: ExperienceRecord) -> ExperienceRecord:
        """子类实现的实际存储逻辑。"""
        raise NotImplementedError

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        raise NotImplementedError

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def list_all(self) -> list[ExperienceRecord]:
        raise NotImplementedError

    async def delete(self, experience_id: str) -> bool:
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Governance pipeline
    # ------------------------------------------------------------------ #

    async def _apply_quality_filter(
        self,
        record: ExperienceRecord,
        *,
        input_message: str | None = None,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> tuple[bool, str]:
        """应用 quality_filter 并记录 metrics。"""
        cfg = governance_config or self._governance_config
        passed, reason = quality_filter(record, config=cfg, input_message=input_message)
        if not passed:
            logger.warning(
                "quality_filter rejected experience %s: %s",
                record.experience_id,
                reason,
            )
            self._metrics.record_quality_rejected(tenant_id=record.tenant_id)
        return passed, reason

    async def _apply_dedup_filter(
        self,
        record: ExperienceRecord,
        existing_records: list[ExperienceRecord],
    ) -> tuple[str, str | None]:
        """应用 dedup_filter 并记录 metrics。"""
        action, merged_id = dedup_filter(
            record.embedding, existing_records, config=self._governance_config
        )
        if action == "skip":
            logger.info(
                "dedup_filter skipped experience %s (sim >= .95, target=%s)",
                record.experience_id,
                merged_id,
            )
            self._metrics.record_dedup_skipped(tenant_id=record.tenant_id)
        elif action == "merge_lessons":
            logger.info(
                "dedup_filter merged experience %s into %s",
                record.experience_id,
                merged_id,
            )
            self._metrics.record_dedup_merged(tenant_id=record.tenant_id)
        return action, merged_id


# ---------------------------------------------------------------------------
# 内存实现
# ---------------------------------------------------------------------------


class InMemoryExperienceStore(ExperienceStore):
    """线程安全的内存经验库。"""

    def __init__(
        self,
        *,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> None:
        super().__init__(governance_config=governance_config)
        self._lock = threading.RLock()
        self._store: dict[str, ExperienceRecord] = {}
        # task_signature -> [experience_id, ...] 索引
        self._sig_index: dict[str, list[str]] = defaultdict(list)

    async def store(
        self,
        record: ExperienceRecord,
        *,
        input_message: str | None = None,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> ExperienceRecord:
        """存储一条经验（含 quality_filter + dedup_filter 治理 pipeline）。"""
        # L1: quality_filter
        passed, _reason = await self._apply_quality_filter(
            record,
            input_message=input_message,
            governance_config=governance_config,
        )
        if not passed:
            return record  # still return the record, but don't store

        # L2: dedup_filter — scan existing records with same task_signature
        existing = list(self._store.values())
        action, merged_id = await self._apply_dedup_filter(record, existing)

        if action == "skip":
            return record  # completely duplicate, don't write

        if action == "merge_lessons":
            with self._lock:
                target = self._store.get(merged_id) if merged_id else None
                if target is not None:
                    # Append new lessons to existing record
                    sep = "\n" if target.lessons and not target.lessons.endswith("\n") else ""
                    target.lessons += f"{sep}{record.lessons}"
                    target.access_count += 1
                    target.last_accessed_at = time.time()
                    self._store[merged_id] = target
                    self._metrics.record_store(tenant_id=record.tenant_id)
                return record

        # action == "store": normal insert
        return await self._store_inner(record)

    async def _store_inner(self, record: ExperienceRecord) -> ExperienceRecord:
        """直接存储，绕过 governance pipeline。"""
        with self._lock:
            self._store[record.experience_id] = record
            sig_list = self._sig_index[record.task_signature]
            if record.experience_id not in sig_list:
                sig_list.append(record.experience_id)
        self._metrics.record_store(tenant_id=record.tenant_id)
        return record

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        """按 ID 查询经验，自动更新访问计数。"""
        with self._lock:
            r = self._store.get(experience_id)
            if r is None:
                return None
            r.access_count += 1
            r.last_accessed_at = time.time()
            return r

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        """检索相似经验。

        - 若 task_embedding 提供 → 用 cosine similarity 排序
        - 否则 → 用 task_signature 精确匹配
        """
        with self._lock:
            if task_embedding is not None:
                # embedding 语义检索：扫所有 records，算 cosine
                candidates = list(self._store.values())
            else:
                # 降级：task_signature 精确匹配
                ids = list(self._sig_index.get(task_signature, []))
                candidates = [self._store[eid] for eid in ids if eid in self._store]

        if task_embedding is not None and candidates:
            # cosine similarity 排序 + 加权
            scored = []
            for r in candidates:
                if r.embedding is None:
                    continue
                score = _cosine_similarity(task_embedding, r.embedding)
                scored.append((r, score))
            scored = _apply_weighted_score_experience(scored)
            scored.sort(key=lambda x: x[1], reverse=True)
            results = [r for r, _ in scored[:top_k]]
            # Auto-update access tracking
            now = time.time()
            for r in results:
                r.access_count += 1
                r.last_accessed_at = now
            return results

        # 无 embedding 或降级路径：按 created_at 倒序
        candidates.sort(key=lambda r: r.created_at, reverse=True)
        return candidates[:top_k]

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        """按 goal substring 模糊匹配。"""
        sig = compute_task_signature(goal)
        exact = await self.retrieve_similar(sig, top_k=top_k)
        if exact:
            return exact

        goal_lower = goal.lower()
        with self._lock:
            candidates = list(self._store.values())
        matched = [
            r for r in candidates if goal_lower in r.goal.lower() or r.goal.lower() in goal_lower
        ]
        matched.sort(key=lambda r: r.created_at, reverse=True)
        return matched[:top_k]

    async def list_all(self) -> list[ExperienceRecord]:
        with self._lock:
            return list(self._store.values())

    async def delete(self, experience_id: str) -> bool:
        with self._lock:
            record = self._store.pop(experience_id, None)
            if record is None:
                return False
            sig_list = self._sig_index.get(record.task_signature, [])
            if experience_id in sig_list:
                sig_list.remove(experience_id)
            return True


# ---------------------------------------------------------------------------
# Postgres 实现
# ---------------------------------------------------------------------------


class PostgresExperienceStore(ExperienceStore):
    """Postgres 持久化经验库。

    Schema:
        experiences(experience_id, tenant_id, task_signature, goal,
                    plan_json, tool_calls_json, outcome, lessons,
                    embedding, created_at)
    """

    def __init__(
        self,
        database_url: str,
        *,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> None:
        super().__init__(governance_config=governance_config)
        self._url = database_url
        self._conn = self._connect()
        self._ensure_schema()

    def _connect(self) -> Any:
        import psycopg  # type: ignore[import-untyped]
        from psycopg.rows import dict_row

        return psycopg.connect(self._url, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        """创建表和索引（IF NOT EXISTS）。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    task_signature TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    plan_json JSONB NOT NULL,
                    tool_calls_json JSONB NOT NULL,
                    outcome TEXT NOT NULL,
                    lessons TEXT NOT NULL,
                    embedding JSONB,
                    created_at DOUBLE PRECISION NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed_at DOUBLE PRECISION,
                    weight DOUBLE PRECISION DEFAULT 1.0
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiences_tenant ON experiences(tenant_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiences_signature ON experiences(task_signature)"
            )
        self._conn.commit()

    async def store(
        self,
        record: ExperienceRecord,
        *,
        input_message: str | None = None,
        governance_config: MemoryGovernanceConfig | None = None,
    ) -> ExperienceRecord:
        """存储一条经验（含 quality_filter + dedup_filter 治理 pipeline）。"""
        # L1: quality_filter
        passed, _reason = await self._apply_quality_filter(
            record,
            input_message=input_message,
            governance_config=governance_config,
        )
        if not passed:
            return record

        # L2: dedup_filter — scan existing records
        existing = self._list_all_inner()
        action, merged_id = await self._apply_dedup_filter(record, existing)

        if action == "skip":
            return record

        if action == "merge_lessons":
            if merged_id is not None:
                # Fetch the existing record and update it
                existing_record = await self.get(merged_id)
                if existing_record is not None:
                    sep = (
                        "\n"
                        if existing_record.lessons and not existing_record.lessons.endswith("\n")
                        else ""
                    )
                    existing_record.lessons += f"{sep}{record.lessons}"
                    existing_record.access_count += 1
                    existing_record.last_accessed_at = time.time()
                    # Write back the merged record
                    await self._store_inner(existing_record)
                    self._metrics.record_store(tenant_id=record.tenant_id)
            return record

        return await self._store_inner(record)

    async def _store_inner(self, record: ExperienceRecord) -> ExperienceRecord:
        """直接存储，绕过 governance pipeline。"""
        plan_json = record.plan.model_dump() if hasattr(record.plan, "model_dump") else record.plan
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO experiences
                    (experience_id, tenant_id, task_signature, goal,
                     plan_json, tool_calls_json, outcome, lessons, embedding, created_at,
                     access_count, last_accessed_at, weight)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (experience_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    task_signature = EXCLUDED.task_signature,
                    goal = EXCLUDED.goal,
                    plan_json = EXCLUDED.plan_json,
                    tool_calls_json = EXCLUDED.tool_calls_json,
                    outcome = EXCLUDED.outcome,
                    lessons = EXCLUDED.lessons,
                    embedding = EXCLUDED.embedding,
                    created_at = EXCLUDED.created_at,
                    access_count = EXCLUDED.access_count,
                    last_accessed_at = EXCLUDED.last_accessed_at,
                    weight = EXCLUDED.weight
                """,
                (
                    record.experience_id,
                    record.tenant_id,
                    record.task_signature,
                    record.goal,
                    json.dumps(plan_json),
                    json.dumps(record.tool_calls),
                    record.outcome,
                    record.lessons,
                    json.dumps(record.embedding) if record.embedding else None,
                    record.created_at,
                    record.access_count,
                    record.last_accessed_at,
                    record.weight,
                ),
            )
        self._conn.commit()
        return record

    def _list_all_inner(self) -> list[ExperienceRecord]:
        """同步获取所有记录（用于 dedup_filter 检索）。"""
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM experiences ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def get(self, experience_id: str) -> ExperienceRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE experience_id = %s",
                (experience_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        record = ExperienceRecord.from_row(row)
        # Auto-update access tracking
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE experiences
                SET access_count = access_count + 1,
                    last_accessed_at = %s
                WHERE experience_id = %s
                """,
                (time.time(), experience_id),
            )
        self._conn.commit()
        record.access_count += 1
        record.last_accessed_at = time.time()
        return record

    async def retrieve_similar(
        self,
        task_signature: str,
        task_embedding: list[float] | None = None,
        top_k: int = 3,
    ) -> list[ExperienceRecord]:
        if task_embedding is not None:
            # embedding 检索：取所有同 signature 的，Python 算 cosine
            # 优化：先按 signature 过滤减少数据量，再算 cosine
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM experiences WHERE task_signature = %s ORDER BY created_at DESC",
                    (task_signature,),
                )
                rows = cur.fetchall()
            if not rows:
                # 降级到全表扫
                with self._conn.cursor() as cur:
                    cur.execute("SELECT * FROM experiences ORDER BY created_at DESC LIMIT 100")
                    rows = cur.fetchall()
            records = [ExperienceRecord.from_row(r) for r in rows]
            scored = []
            for r in records:
                if r.embedding is None:
                    continue
                score = _cosine_similarity(task_embedding, r.embedding)
                scored.append((r, score))
            scored = _apply_weighted_score_experience(scored)
            scored.sort(key=lambda x: x[1], reverse=True)
            results = [r for r, _ in scored[:top_k]]
            # Auto-update access tracking
            now = time.time()
            for r in results:
                r.access_count += 1
                r.last_accessed_at = now
            return results

        # 无 embedding：signature 精确匹配
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE task_signature = %s ORDER BY created_at DESC LIMIT %s",
                (task_signature, top_k),
            )
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def retrieve_by_goal(self, goal: str, top_k: int = 3) -> list[ExperienceRecord]:
        sig = compute_task_signature(goal)
        exact = await self.retrieve_similar(sig, top_k=top_k)
        if exact:
            return exact
        # 降级：ILIKE 模糊匹配
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiences WHERE goal ILIKE %s ORDER BY created_at DESC LIMIT %s",
                (f"%{goal}%", top_k),
            )
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def list_all(self) -> list[ExperienceRecord]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM experiences ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [ExperienceRecord.from_row(r) for r in rows]

    async def delete(self, experience_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM experiences WHERE experience_id = %s",
                (experience_id,),
            )
            deleted = cur.rowcount
        self._conn.commit()
        return deleted > 0


# ---------------------------------------------------------------------------
# cosine similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的 cosine similarity。维度不匹配返回 0。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _apply_weighted_score_experience(
    scored: list[tuple[ExperienceRecord, float]],
) -> list[tuple[ExperienceRecord, float]]:
    """Convert raw similarity scores to weighted final scores.

    final_score = similarity * 0.7 + normalized_weight * 0.3
    normalized_weight = min(1.0, weight / max_weight_in_results)
    """
    if not scored:
        return scored
    max_weight = max(r.weight for r, _ in scored)
    if max_weight <= 0:
        max_weight = 1.0
    result: list[tuple[ExperienceRecord, float]] = []
    for r, sim in scored:
        norm_w = min(1.0, r.weight / max_weight)
        final_score = sim * 0.7 + norm_w * 0.3
        result.append((r, final_score))
    return result


# ---------------------------------------------------------------------------
# 全局单例 + backend 自动选择
# ---------------------------------------------------------------------------


_store: ExperienceStore | None = None
_store_lock = threading.Lock()


def get_experience_store() -> ExperienceStore:
    """获取全局 ExperienceStore 单例。

    Backend 选择：
    1. DATABASE_URL 可达 → PostgresExperienceStore
    2. 否则 → InMemoryExperienceStore
    """
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            database_url = os.environ.get("DATABASE_URL", "")
            if database_url:
                try:
                    _store = PostgresExperienceStore(database_url)
                    logger.info("experience store backend=postgres")
                except Exception as e:
                    logger.warning("postgres 不可达，回退内存 experience store: %s", e)
                    _store = InMemoryExperienceStore()
                    logger.info("experience store backend=memory")
            else:
                _store = InMemoryExperienceStore()
                logger.info("experience store backend=memory")
    return _store


def reset_experience_store_for_tests() -> None:
    """重置全局单例（仅测试使用）。"""
    global _store
    with _store_lock:
        _store = None


# ---------------------------------------------------------------------------
# 顶层便捷函数（async）
# ---------------------------------------------------------------------------


async def store_experience(
    record: ExperienceRecord,
    *,
    input_message: str | None = None,
    governance_config: MemoryGovernanceConfig | None = None,
) -> ExperienceRecord:
    """存储经验到全局 store。"""
    return await get_experience_store().store(
        record,
        input_message=input_message,
        governance_config=governance_config,
    )


async def retrieve_similar_experiences(
    task_signature: str,
    task_embedding: list[float] | None = None,
    top_k: int = 3,
) -> list[ExperienceRecord]:
    """从全局 store 检索相似经验。"""
    return await get_experience_store().retrieve_similar(
        task_signature, task_embedding=task_embedding, top_k=top_k
    )


async def compute_task_embedding(goal: str) -> list[float] | None:
    """调 EmbeddingService 计算 goal 的 embedding。失败返回 None。"""
    try:
        from packages.embedding.service import get_embedding_service

        service = get_embedding_service()
        if service is None:
            return None
        from packages.platform import get_settings

        settings = get_settings()
        model_id = "text-embedding-3-small"
        # 尝试从 settings 读 embedding_model
        emb_model = getattr(settings, "embedding_model", None)
        if emb_model:
            model_id = emb_model
        return await service.embed_one(model_id, goal, tenant_id="system")
    except Exception as exc:
        logger.warning("compute_task_embedding failed: %s", exc)
        return None


def new_experience_id() -> str:
    """生成新的 UUID 作为经验 ID。"""
    return str(uuid.uuid4())


def compute_task_signature(goal: str) -> str:
    """计算任务签名：goal 的 SHA1 前 16 字符，lowercase。"""
    return hashlib.sha1(goal.strip().encode("utf-8")).hexdigest()[:16]


def build_experience_record(
    *,
    tenant_id: str,
    goal: str,
    plan: AgentPlan,
    tool_calls: list[dict[str, Any]] | None = None,
    outcome: str = "success",
    lessons: str = "",
    embedding: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExperienceRecord:
    """便捷工厂：创建 ExperienceRecord（自动生成 ID、签名、时间戳）。"""
    return ExperienceRecord(
        experience_id=new_experience_id(),
        tenant_id=tenant_id,
        task_signature=compute_task_signature(goal),
        goal=goal,
        plan=plan,
        tool_calls=tool_calls or [],
        outcome=outcome,
        lessons=lessons,
        created_at=time.time(),
        embedding=embedding,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Rerank（召回二次校验，PRD-3）
# ---------------------------------------------------------------------------


_rerank_cache: dict[str, tuple[float, list[int]]] = {}
_RERANK_CACHE_TTL: float = 300.0  # 5 分钟


async def rerank_experiences(
    goal: str,
    experiences: list[ExperienceRecord],
    max_relevant: int = 2,
    model: str | None = None,
    *,
    counter: list[int] | None = None,
) -> list[ExperienceRecord]:
    """LLM judge: filter experiences to only those relevant to goal.

    LLM judge prompt:
        你是经验筛选助手。以下是当前任务目标以及一些历史经验。
        请判断每条经验是否真正与当前任务相关。
        输出 JSON 格式：[{"index": 0, "relevant": true, "reason": "..."}, ...]

    Args:
        goal: 当前任务目标。
        experiences: 候选经验列表。
        max_relevant: 最多返回条数。
        model: 可选模型名。
        counter: 可选 LLM 调用计数器，同 self_refine._call_llm 模式。

    Returns:
        最多 max_relevant 条经验。fail-open 模式下返回全部候选。
    """
    if not experiences:
        return []

    # 缓存检查
    cache_hit, cached_indices = _check_rerank_cache(goal, experiences)
    if cache_hit and cached_indices is not None:
        logger.debug("rerank_experiences cache hit for goal=%s", goal[:40])
        return [experiences[i] for i in cached_indices if i < len(experiences)][:max_relevant]

    try:
        relevant_indices = await _call_rerank_llm(goal, experiences, model=model, counter=counter)

        if not relevant_indices:
            # fail-open: empty result -> return all
            logger.warning("rerank_experiences: LLM returned empty results, returning all")
            _set_rerank_cache(goal, experiences, list(range(len(experiences))))
            return experiences[:max_relevant]

        # 缓存结果
        _set_rerank_cache(goal, experiences, relevant_indices)

        # 只保留 relevant=True 的，再截断
        result = [experiences[i] for i in relevant_indices if i < len(experiences)]
        return result[:max_relevant]

    except Exception as exc:
        logger.warning("rerank_experiences failed, returning all: %s", exc)
        return experiences[:max_relevant]


def _rerank_cache_key(goal: str, experiences: list[ExperienceRecord]) -> str:
    """生成缓存 key: sha1(goal) + sha1(sorted exp_ids)."""
    goal_hash = hashlib.sha1(goal.encode("utf-8")).hexdigest()
    exp_ids = sorted(e.experience_id for e in experiences)
    ids_hash = hashlib.sha1("|".join(exp_ids).encode("utf-8")).hexdigest()
    return f"rerank:{goal_hash}:{ids_hash}"


def _check_rerank_cache(
    goal: str,
    experiences: list[ExperienceRecord],
) -> tuple[bool, list[int] | None]:
    """检查缓存是否命中且未过期。返回 (hit, indices)。"""
    key = _rerank_cache_key(goal, experiences)
    entry = _rerank_cache.get(key)
    if entry is None:
        return False, None
    expires_at, indices = entry
    if time.time() > expires_at:
        del _rerank_cache[key]
        return False, None
    return True, indices


def _set_rerank_cache(
    goal: str,
    experiences: list[ExperienceRecord],
    indices: list[int],
) -> None:
    """设置缓存。"""
    key = _rerank_cache_key(goal, experiences)
    _rerank_cache[key] = (time.time() + _RERANK_CACHE_TTL, indices)
    # 限制缓存大小
    if len(_rerank_cache) > 1000:
        now = time.time()
        expired = [k for k, (exp, _) in _rerank_cache.items() if now > exp]
        for k in expired:
            del _rerank_cache[k]


def clear_rerank_cache() -> None:
    """清理缓存（用于测试和运维）。"""
    _rerank_cache.clear()


async def _call_rerank_llm(
    goal: str,
    experiences: list[ExperienceRecord],
    *,
    model: str | None = None,
    counter: list[int] | None = None,
) -> list[int]:
    """调用 LLM judge，返回 relevant 条目的原始索引列表。

    与 self_refine._call_llm 使用相同的 forward_with_model_router 模式。
    """
    from packages.platform import forward_with_model_router

    # 构建 user prompt
    lines: list[str] = [
        "当前任务目标：",
        goal,
        "",
        "历史经验列表：",
    ]
    for i, exp in enumerate(experiences):
        lines.append(f"  [{i}] goal: {exp.goal}")
        lines.append(f"      outcome: {exp.outcome}")
        lines.append(f"      lessons: {exp.lessons}")
    lines.append("")
    lines.append(
        "请判断每条经验是否真正与当前任务相关。"
        '输出 JSON 格式：[{"index": 0, "relevant": true, "reason": "..."}, ...]'
    )
    user = "\n".join(lines)

    system = (
        "你是经验筛选助手。以下是当前任务目标以及一些历史经验。"
        "请判断每条经验是否真正与当前任务相关。"
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
    }
    route = await forward_with_model_router(payload)
    if counter is not None:
        counter[0] += 1

    if route.status != 200 or not route.body:
        raise RuntimeError(f"LLM call failed with status={route.status}")

    choices = route.body.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned empty choices")

    content = (choices[0].get("message") or {}).get("content") or ""
    if not content.strip():
        raise RuntimeError("LLM returned empty content")

    # Parse JSON from response
    relevant_indices = _parse_rerank_json(content, len(experiences))
    return relevant_indices


def _parse_rerank_json(content: str, expected_count: int) -> list[int]:
    """从 LLM 响应中解析 JSON 数组，返回 relevant=True 的索引列表。

    fail-hard：解析失败时抛出异常，由调用方处理降级。
    """
    import re as _re

    # 尝试从 ```json ... ``` 或 ``` ... ``` 块中提取
    json_match = _re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
    if json_match:
        content = json_match.group(1).strip()

    # 尝试从纯 JSON 数组中提取
    array_match = _re.search(r"\[\s*\{.*?\}\s*\]", content, _re.DOTALL)
    if array_match:
        content = array_match.group(0)

    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")

    indices: list[int] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("relevant") is True:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < expected_count:
                indices.append(idx)

    return indices
