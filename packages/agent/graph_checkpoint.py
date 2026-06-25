"""图执行 checkpoint 存储 — 内存 + 可选 Redis（不可达时回退内存）。"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger("ai_platform.graph_checkpoint")

CheckpointStatus = Literal["running", "paused", "completed", "failed"]

_REDIS_KEY_PREFIX = "ai_platform:graph_checkpoint:"
_REDIS_TENANT_INDEX_PREFIX = "ai_platform:graph_checkpoint:tenant:"


@dataclass
class WorkflowExecutionCheckpoint:
    execution_id: str
    tenant_id: str
    workflow_id: str
    status: CheckpointStatus
    current_node: str | None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "current_node": self.current_node,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "variables": self.variables,
            "trace": self.trace,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowExecutionCheckpoint:
        return cls(
            execution_id=str(data["execution_id"]),
            tenant_id=str(data["tenant_id"]),
            workflow_id=str(data["workflow_id"]),
            status=data["status"],  # type: ignore[arg-type]
            current_node=data.get("current_node"),
            inputs=dict(data.get("inputs") or {}),
            outputs=dict(data.get("outputs") or {}),
            variables=dict(data.get("variables") or {}),
            trace=list(data.get("trace") or []),
            error=data.get("error"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )


class GraphCheckpointStore:
    def create(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        inputs: dict[str, Any],
        start_node: str,
    ) -> WorkflowExecutionCheckpoint:
        raise NotImplementedError

    def get(self, execution_id: str) -> WorkflowExecutionCheckpoint | None:
        raise NotImplementedError

    def save(self, checkpoint: WorkflowExecutionCheckpoint) -> None:
        raise NotImplementedError

    def list_resumable(self, tenant_id: str) -> list[WorkflowExecutionCheckpoint]:
        raise NotImplementedError


class InMemoryGraphCheckpointStore(GraphCheckpointStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, WorkflowExecutionCheckpoint] = {}

    def create(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        inputs: dict[str, Any],
        start_node: str,
    ) -> WorkflowExecutionCheckpoint:
        cp = WorkflowExecutionCheckpoint(
            execution_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="running",
            current_node=start_node,
            inputs=dict(inputs),
        )
        with self._lock:
            self._store[cp.execution_id] = cp
        return cp

    def get(self, execution_id: str) -> WorkflowExecutionCheckpoint | None:
        with self._lock:
            return self._store.get(execution_id)

    def save(self, checkpoint: WorkflowExecutionCheckpoint) -> None:
        checkpoint.updated_at = time.time()
        with self._lock:
            self._store[checkpoint.execution_id] = checkpoint

    def list_resumable(self, tenant_id: str) -> list[WorkflowExecutionCheckpoint]:
        with self._lock:
            return [
                cp
                for cp in self._store.values()
                if cp.tenant_id == tenant_id and cp.status in ("failed", "paused", "running")
            ]


class RedisGraphCheckpointStore(GraphCheckpointStore):
    def __init__(self, redis_url: str) -> None:
        from packages.state.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)

    def _key(self, execution_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{execution_id}"

    def _tenant_key(self, tenant_id: str) -> str:
        return f"{_REDIS_TENANT_INDEX_PREFIX}{tenant_id}"

    def create(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        inputs: dict[str, Any],
        start_node: str,
    ) -> WorkflowExecutionCheckpoint:
        cp = WorkflowExecutionCheckpoint(
            execution_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            status="running",
            current_node=start_node,
            inputs=dict(inputs),
        )
        self.save(cp)
        return cp

    def get(self, execution_id: str) -> WorkflowExecutionCheckpoint | None:
        raw = self._redis.get(self._key(execution_id))
        if not raw:
            return None
        return WorkflowExecutionCheckpoint.from_dict(json.loads(raw))

    def save(self, checkpoint: WorkflowExecutionCheckpoint) -> None:
        checkpoint.updated_at = time.time()
        payload = json.dumps(checkpoint.to_dict(), ensure_ascii=False)
        self._redis.set(self._key(checkpoint.execution_id), payload)
        self._redis.sadd(self._tenant_key(checkpoint.tenant_id), checkpoint.execution_id)

    def list_resumable(self, tenant_id: str) -> list[WorkflowExecutionCheckpoint]:
        ids = self._redis.smembers(self._tenant_key(tenant_id)) or []
        result: list[WorkflowExecutionCheckpoint] = []
        for eid in ids:
            cp = self.get(str(eid))
            if cp and cp.status in ("failed", "paused", "running"):
                result.append(cp)
        return result


_store: GraphCheckpointStore | None = None
_resolved_backend: str | None = None


def get_graph_checkpoint_store() -> GraphCheckpointStore:
    global _store
    if _store is None:
        _store = InMemoryGraphCheckpointStore()
    return _store


def resolve_graph_checkpoint_store(redis_url: str | None = None) -> GraphCheckpointStore:
    """REDIS_URL 可达时用 Redis，否则进程内内存。"""
    global _store, _resolved_backend
    from packages.state.redis_client import get_effective_redis_url

    effective = get_effective_redis_url(redis_url)
    backend = f"redis:{effective}" if effective else "memory"
    if _store is not None and _resolved_backend == backend:
        return _store
    if effective:
        _store = RedisGraphCheckpointStore(effective)
        _resolved_backend = backend
        logger.info("graph checkpoint backend=redis")
    else:
        _store = InMemoryGraphCheckpointStore()
        _resolved_backend = backend
        logger.info("graph checkpoint backend=memory")
    return _store


def reset_graph_checkpoint_store_for_tests() -> None:
    global _store, _resolved_backend
    _store = None
    _resolved_backend = None
