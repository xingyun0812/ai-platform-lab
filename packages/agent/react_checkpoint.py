"""packages/agent/react_checkpoint.py — S2: ReAct loop checkpoint storage & injection.

Checkpoints serialize the ReAct loop state at each tool round so a paused or
interrupted task can be resumed from its last-known-good step.
Follows the same ABC + InMemory + Postgres pattern as LongRunTaskStore.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ai_platform.agent.react_checkpoint")

__all__ = [
    "ReactCheckpoint",
    "ReactCheckpointStore",
    "InMemoryReactCheckpointStore",
    "PostgresReactCheckpointStore",
    "get_react_checkpoint_store",
    "reset_react_checkpoint_store_for_tests",
    "save_react_checkpoint",
    "load_latest_react_checkpoint",
    "list_react_checkpoints",
    "delete_task_react_checkpoints",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ReactCheckpoint:
    """Checkpoint capturing ReAct loop state after a tool call round."""

    checkpoint_id: str
    task_id: str
    step: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    reasoning_trace: list[dict[str, Any]] = field(default_factory=list)
    reflect_remaining: int = 0
    runtime_truncated_tools: int = 0
    budget_meta: dict[str, Any] = field(default_factory=dict)
    resolved_model: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "step": self.step,
            "messages": list(self.messages),
            "trace": list(self.trace),
            "reasoning_trace": list(self.reasoning_trace),
            "reflect_remaining": self.reflect_remaining,
            "runtime_truncated_tools": self.runtime_truncated_tools,
            "budget_meta": dict(self.budget_meta),
            "resolved_model": self.resolved_model,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReactCheckpoint:
        return cls(
            checkpoint_id=d["checkpoint_id"],
            task_id=d["task_id"],
            step=int(d.get("step", 0)),
            messages=list(d.get("messages", [])),
            trace=list(d.get("trace", [])),
            reasoning_trace=list(d.get("reasoning_trace", [])),
            reflect_remaining=int(d.get("reflect_remaining", 0)),
            runtime_truncated_tools=int(d.get("runtime_truncated_tools", 0)),
            budget_meta=dict(d.get("budget_meta", {})),
            resolved_model=str(d.get("resolved_model", "")),
            created_at=float(d.get("created_at", 0.0)),
        )


# ---------------------------------------------------------------------------
# Abstract base store
# ---------------------------------------------------------------------------


class ReactCheckpointStore:
    """ReAct checkpoint storage abstract base class."""

    async def save(self, checkpoint: ReactCheckpoint) -> str:
        raise NotImplementedError

    async def load_latest(self, task_id: str) -> ReactCheckpoint | None:
        raise NotImplementedError

    async def list_checkpoints(self, task_id: str, limit: int = 10) -> list[ReactCheckpoint]:
        raise NotImplementedError

    async def delete_task_checkpoints(self, task_id: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryReactCheckpointStore
# ---------------------------------------------------------------------------


class InMemoryReactCheckpointStore(ReactCheckpointStore):
    """Thread-safe in-memory ReAct checkpoint storage."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._checkpoints: dict[str, list[ReactCheckpoint]] = defaultdict(list)

    async def save(self, checkpoint: ReactCheckpoint) -> str:
        with self._lock:
            self._checkpoints[checkpoint.task_id].append(checkpoint)
        return checkpoint.checkpoint_id

    async def load_latest(self, task_id: str) -> ReactCheckpoint | None:
        with self._lock:
            cps = self._checkpoints.get(task_id, [])
            if not cps:
                return None
            return cps[-1]

    async def list_checkpoints(self, task_id: str, limit: int = 10) -> list[ReactCheckpoint]:
        with self._lock:
            cps = list(self._checkpoints.get(task_id, []))
            return cps[-limit:]

    async def delete_task_checkpoints(self, task_id: str) -> None:
        with self._lock:
            self._checkpoints.pop(task_id, None)


# ---------------------------------------------------------------------------
# PostgresReactCheckpointStore
# ---------------------------------------------------------------------------


class PostgresReactCheckpointStore(ReactCheckpointStore):
    """Postgres-persisted ReAct checkpoint storage."""

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS react_loop_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        step INTEGER NOT NULL,
        messages_json JSONB NOT NULL,
        trace_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        reasoning_trace_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        reflect_remaining INTEGER NOT NULL DEFAULT 0,
        runtime_truncated_tools INTEGER NOT NULL DEFAULT 0,
        budget_meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        resolved_model TEXT NOT NULL DEFAULT '',
        created_at DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (task_id) REFERENCES long_run_tasks(task_id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_react_cp_task_step
        ON react_loop_checkpoints(task_id, step DESC);
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._conn = None
        self._lock = threading.RLock()
        self._connect()
        self._ensure_schema()

    def _connect(self) -> None:
        import psycopg  # type: ignore[import-untyped]
        from psycopg.rows import dict_row  # type: ignore[import-untyped]

        self._conn = psycopg.connect(self._database_url, row_factory=dict_row)
        self._conn.autocommit = True

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(self._SCHEMA_SQL)

    def _row_to_checkpoint(self, row: dict[str, Any]) -> ReactCheckpoint:
        def _load_json(val: Any) -> Any:
            if isinstance(val, str):
                return json.loads(val) if val else []
            return val or []

        return ReactCheckpoint(
            checkpoint_id=row["checkpoint_id"],
            task_id=row["task_id"],
            step=int(row["step"]),
            messages=list(_load_json(row.get("messages_json", []))),
            trace=list(_load_json(row.get("trace_json", []))),
            reasoning_trace=list(_load_json(row.get("reasoning_trace_json", []))),
            reflect_remaining=int(row.get("reflect_remaining", 0)),
            runtime_truncated_tools=int(row.get("runtime_truncated_tools", 0)),
            budget_meta=dict(_load_json(row.get("budget_meta_json", {}))),
            resolved_model=str(row.get("resolved_model", "")),
            created_at=float(row.get("created_at", 0.0)),
        )

    async def save(self, checkpoint: ReactCheckpoint) -> str:
        messages_json = json.dumps(checkpoint.messages)
        trace_json = json.dumps(checkpoint.trace)
        reasoning_trace_json = json.dumps(checkpoint.reasoning_trace)
        budget_meta_json = json.dumps(checkpoint.budget_meta)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO react_loop_checkpoints
                        (checkpoint_id, task_id, step,
                         messages_json, trace_json, reasoning_trace_json,
                         reflect_remaining, runtime_truncated_tools,
                         budget_meta_json, resolved_model, created_at)
                    VALUES (%s, %s, %s,
                            %s::jsonb, %s::jsonb, %s::jsonb,
                            %s, %s,
                            %s::jsonb, %s, %s)
                    ON CONFLICT (checkpoint_id) DO NOTHING
                    """,
                    (
                        checkpoint.checkpoint_id,
                        checkpoint.task_id,
                        checkpoint.step,
                        messages_json,
                        trace_json,
                        reasoning_trace_json,
                        checkpoint.reflect_remaining,
                        checkpoint.runtime_truncated_tools,
                        budget_meta_json,
                        checkpoint.resolved_model,
                        checkpoint.created_at,
                    ),
                )
        return checkpoint.checkpoint_id

    async def load_latest(self, task_id: str) -> ReactCheckpoint | None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM react_loop_checkpoints
                    WHERE task_id = %s
                    ORDER BY step DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._row_to_checkpoint(row)

    async def list_checkpoints(self, task_id: str, limit: int = 10) -> list[ReactCheckpoint]:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM react_loop_checkpoints
                    WHERE task_id = %s
                    ORDER BY step DESC
                    LIMIT %s
                    """,
                    (task_id, limit),
                )
                rows = cur.fetchall()
                return [self._row_to_checkpoint(r) for r in rows]

    async def delete_task_checkpoints(self, task_id: str) -> None:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM react_loop_checkpoints WHERE task_id = %s",
                    (task_id,),
                )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global singleton — auto backend selection
# ---------------------------------------------------------------------------

_store: ReactCheckpointStore | None = None
_store_lock = threading.Lock()


def get_react_checkpoint_store() -> ReactCheckpointStore:
    """Auto-select backend: DATABASE_URL -> Postgres, else InMemory."""
    global _store
    with _store_lock:
        if _store is not None:
            return _store

        database_url = os.environ.get("DATABASE_URL", "")
        if database_url:
            try:
                _store = PostgresReactCheckpointStore(database_url)
                logger.info("react_checkpoint store backend=postgres")
            except Exception as e:
                logger.warning("postgres unreachable, falling back to memory: %s", e)
                _store = InMemoryReactCheckpointStore()
        else:
            _store = InMemoryReactCheckpointStore()
            logger.info("react_checkpoint store backend=memory")

        return _store


def reset_react_checkpoint_store_for_tests() -> None:
    """Reset the singleton store — used in tests."""
    global _store
    with _store_lock:
        if _store is not None and hasattr(_store, "close"):
            try:
                _store.close()
            except Exception:
                pass
        _store = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_checkpoint_id() -> str:
    return str(uuid.uuid4())


def _serialize_trace(trace: list[Any]) -> list[dict[str, Any]]:
    """Convert ToolCallRecord / ReasoningTraceRecord objects to dicts."""
    result: list[dict[str, Any]] = []
    if not trace:
        return result
    for t in trace:
        if hasattr(t, "model_dump"):
            result.append(t.model_dump())
        elif hasattr(t, "to_dict"):
            result.append(t.to_dict())
        elif isinstance(t, dict):
            result.append(t)
        else:
            result.append(dict(t))
    return result


def _serialize_budget_meta(budget_meta: Any) -> dict[str, Any]:
    """Convert ContextBudgetMeta dataclass to dict."""
    if budget_meta is None:
        return {}
    if hasattr(budget_meta, "__dataclass_fields__"):
        return {f.name: getattr(budget_meta, f.name) for f in budget_meta.__dataclass_fields__.values()}
    if isinstance(budget_meta, dict):
        return budget_meta
    return dict(budget_meta)


# ---------------------------------------------------------------------------
# Top-level async convenience functions
# ---------------------------------------------------------------------------


async def save_react_checkpoint(
    task_id: str,
    step: int,
    messages: list[dict[str, Any]],
    *,
    trace: list[Any] | None = None,
    reasoning_trace: list[Any] | None = None,
    reflect_remaining: int = 0,
    runtime_truncated_tools: int = 0,
    budget_meta: Any | None = None,
    resolved_model: str = "",
) -> str:
    """Save a ReAct checkpoint for the given task at the given step."""
    store = get_react_checkpoint_store()
    checkpoint = ReactCheckpoint(
        checkpoint_id=_new_checkpoint_id(),
        task_id=task_id,
        step=step,
        messages=list(messages),
        trace=_serialize_trace(trace),
        reasoning_trace=_serialize_trace(reasoning_trace),
        reflect_remaining=reflect_remaining,
        runtime_truncated_tools=runtime_truncated_tools,
        budget_meta=_serialize_budget_meta(budget_meta),
        resolved_model=resolved_model,
    )
    return await store.save(checkpoint)


async def load_latest_react_checkpoint(task_id: str) -> ReactCheckpoint | None:
    """Load the latest checkpoint for a task."""
    return await get_react_checkpoint_store().load_latest(task_id)


async def list_react_checkpoints(task_id: str, limit: int = 10) -> list[ReactCheckpoint]:
    """List checkpoints for a task, newest first."""
    return await get_react_checkpoint_store().list_checkpoints(task_id, limit)


async def delete_task_react_checkpoints(task_id: str) -> None:
    """Delete all checkpoints for a task."""
    await get_react_checkpoint_store().delete_task_checkpoints(task_id)
