"""反馈存储 — Phase J #48

数据模型 + 存储后端（内存 / SQLite）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("ai_platform.feedback.store")


# ─────────────────────────── Enum ────────────────────────────


class FeedbackType(str, Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    RATING_1 = "rating_1"
    RATING_2 = "rating_2"
    RATING_3 = "rating_3"
    RATING_4 = "rating_4"
    RATING_5 = "rating_5"
    BAD_CASE = "bad_case"


_NEGATIVE_TYPES = {FeedbackType.THUMBS_DOWN, FeedbackType.BAD_CASE, FeedbackType.RATING_1, FeedbackType.RATING_2}


def is_negative(feedback_type: str) -> bool:
    try:
        return FeedbackType(feedback_type) in _NEGATIVE_TYPES
    except ValueError:
        return False


# ─────────────────────────── Dataclass ───────────────────────


@dataclass
class Feedback:
    feedback_id: str
    tenant_id: str
    session_id: str
    message_id: str
    feedback_type: str
    rating: int | None = None
    comment: str | None = None
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


# ─────────────────────────── ABC ─────────────────────────────


class FeedbackStore(ABC):
    @abstractmethod
    async def create(self, feedback: Feedback) -> str: ...

    @abstractmethod
    async def get(self, feedback_id: str) -> Feedback | None: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        feedback_type: str | None = None,
        limit: int = 50,
    ) -> list[Feedback]: ...

    @abstractmethod
    async def list_bad_cases(self, tenant_id: str, limit: int = 50) -> list[Feedback]: ...

    @abstractmethod
    async def count_by_type(self, tenant_id: str) -> dict[str, int]: ...


# ─────────────────────────── InMemory ────────────────────────


class InMemoryFeedbackStore(FeedbackStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, Feedback] = {}

    async def create(self, feedback: Feedback) -> str:
        with self._lock:
            self._store[feedback.feedback_id] = feedback
        return feedback.feedback_id

    async def get(self, feedback_id: str) -> Feedback | None:
        with self._lock:
            return self._store.get(feedback_id)

    async def list(
        self,
        tenant_id: str,
        feedback_type: str | None = None,
        limit: int = 50,
    ) -> list[Feedback]:
        with self._lock:
            items = [
                f for f in self._store.values()
                if f.tenant_id == tenant_id
                and (feedback_type is None or f.feedback_type == feedback_type)
            ]
        items.sort(key=lambda f: f.created_at, reverse=True)
        return items[:limit]

    async def list_bad_cases(self, tenant_id: str, limit: int = 50) -> list[Feedback]:
        negative = {ft.value for ft in _NEGATIVE_TYPES}
        with self._lock:
            items = [
                f for f in self._store.values()
                if f.tenant_id == tenant_id and f.feedback_type in negative
            ]
        items.sort(key=lambda f: f.created_at, reverse=True)
        return items[:limit]

    async def count_by_type(self, tenant_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for f in self._store.values():
                if f.tenant_id == tenant_id:
                    counts[f.feedback_type] = counts.get(f.feedback_type, 0) + 1
        return counts


# ─────────────────────────── SQLite (optional) ───────────────


class SqliteFeedbackStore(FeedbackStore):
    """可选的 SQLite 持久化后端。依赖 aiosqlite。"""

    def __init__(self, database_url: str) -> None:
        # database_url 形如 sqlite:///path/to/db 或直接 file path
        if database_url.startswith("sqlite:///"):
            self._path = database_url[len("sqlite:///"):]
        else:
            self._path = database_url
        self._init_done = False
        self._lock = asyncio.Lock()

    async def _ensure_init(self) -> None:
        if self._init_done:
            return
        try:
            import aiosqlite  # type: ignore

            async with aiosqlite.connect(self._path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback (
                        feedback_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        feedback_type TEXT NOT NULL,
                        rating INTEGER,
                        comment TEXT,
                        user_id TEXT,
                        created_at REAL NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                await db.commit()
            self._init_done = True
        except ImportError:
            logger.warning("aiosqlite not installed; falling back to no-op")
            self._init_done = True

    async def create(self, feedback: Feedback) -> str:
        await self._ensure_init()
        import json as _json
        try:
            import aiosqlite

            async with aiosqlite.connect(self._path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO feedback
                    (feedback_id, tenant_id, session_id, message_id,
                     feedback_type, rating, comment, user_id, created_at, metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        feedback.feedback_id,
                        feedback.tenant_id,
                        feedback.session_id,
                        feedback.message_id,
                        feedback.feedback_type,
                        feedback.rating,
                        feedback.comment,
                        feedback.user_id,
                        feedback.created_at,
                        _json.dumps(feedback.metadata),
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.error("sqlite create error: %s", exc)
        return feedback.feedback_id

    async def get(self, feedback_id: str) -> Feedback | None:
        await self._ensure_init()
        import json as _json
        try:
            import aiosqlite

            async with aiosqlite.connect(self._path) as db:
                async with db.execute(
                    "SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    return self._row_to_feedback(row)
        except Exception as exc:
            logger.error("sqlite get error: %s", exc)
            return None

    def _row_to_feedback(self, row: Any) -> Feedback:
        import json as _json
        return Feedback(
            feedback_id=row[0],
            tenant_id=row[1],
            session_id=row[2],
            message_id=row[3],
            feedback_type=row[4],
            rating=row[5],
            comment=row[6],
            user_id=row[7],
            created_at=float(row[8]),
            metadata=_json.loads(row[9]) if row[9] else {},
        )

    async def list(
        self,
        tenant_id: str,
        feedback_type: str | None = None,
        limit: int = 50,
    ) -> list[Feedback]:
        await self._ensure_init()
        try:
            import aiosqlite

            if feedback_type:
                q = "SELECT * FROM feedback WHERE tenant_id=? AND feedback_type=? ORDER BY created_at DESC LIMIT ?"
                params = (tenant_id, feedback_type, limit)
            else:
                q = "SELECT * FROM feedback WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?"
                params = (tenant_id, limit)
            async with aiosqlite.connect(self._path) as db:
                async with db.execute(q, params) as cursor:
                    rows = await cursor.fetchall()
                    return [self._row_to_feedback(r) for r in rows]
        except Exception as exc:
            logger.error("sqlite list error: %s", exc)
            return []

    async def list_bad_cases(self, tenant_id: str, limit: int = 50) -> list[Feedback]:
        negative = tuple(ft.value for ft in _NEGATIVE_TYPES)
        await self._ensure_init()
        try:
            import aiosqlite

            placeholders = ",".join("?" for _ in negative)
            q = (
                f"SELECT * FROM feedback WHERE tenant_id=? "
                f"AND feedback_type IN ({placeholders}) "
                f"ORDER BY created_at DESC LIMIT ?"
            )
            async with aiosqlite.connect(self._path) as db:
                async with db.execute(q, (tenant_id, *negative, limit)) as cursor:
                    rows = await cursor.fetchall()
                    return [self._row_to_feedback(r) for r in rows]
        except Exception as exc:
            logger.error("sqlite list_bad_cases error: %s", exc)
            return []

    async def count_by_type(self, tenant_id: str) -> dict[str, int]:
        await self._ensure_init()
        try:
            import aiosqlite

            async with aiosqlite.connect(self._path) as db:
                async with db.execute(
                    "SELECT feedback_type, COUNT(*) FROM feedback WHERE tenant_id=? GROUP BY feedback_type",
                    (tenant_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return {row[0]: int(row[1]) for row in rows}
        except Exception as exc:
            logger.error("sqlite count_by_type error: %s", exc)
            return {}


# ─────────────────────────── Singleton ───────────────────────

_store: FeedbackStore | None = None
_store_lock = threading.RLock()


def init_feedback_store(database_url: str | None = None) -> FeedbackStore:
    global _store
    with _store_lock:
        if database_url:
            _store = SqliteFeedbackStore(database_url)
        else:
            _store = InMemoryFeedbackStore()
        return _store


def get_feedback_store() -> FeedbackStore | None:
    return _store


def reset_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
