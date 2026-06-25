"""图执行 checkpoint 存储（内存）— 支持 Orchestrator 断点 resume。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

CheckpointStatus = Literal["running", "paused", "completed", "failed"]


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


class GraphCheckpointStore:
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


_store: GraphCheckpointStore | None = None


def get_graph_checkpoint_store() -> GraphCheckpointStore:
    global _store
    if _store is None:
        _store = GraphCheckpointStore()
    return _store


def reset_graph_checkpoint_store_for_tests() -> None:
    global _store
    _store = None
