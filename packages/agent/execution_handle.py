"""三层 ExecutionHandle 只读状态聚合 — #169 PR-2。

不合并 ADR-0001 三种 ID；仅提供统一查询视图与 resume 入口提示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from packages.agent.graph_checkpoint import GraphCheckpointStore

ExecutionLayer = Literal["plan_approval", "orchestrator", "long_run"]

_RESUMABLE_ORCHESTRATOR = frozenset({"running", "failed", "paused"})
_RESUMABLE_LONG_RUN = frozenset({"pending", "running", "paused"})


@dataclass(frozen=True)
class ResumeHint:
    method: str
    path: str
    notes: str = ""


@dataclass
class ExecutionHandleStatus:
    layer: ExecutionLayer
    handle_id: str
    status: str
    tenant_id: str
    resumable: bool
    resume_hint: ResumeHint | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        hint = None
        if self.resume_hint is not None:
            hint = {
                "method": self.resume_hint.method,
                "path": self.resume_hint.path,
                "notes": self.resume_hint.notes,
            }
        return {
            "layer": self.layer,
            "handle_id": self.handle_id,
            "status": self.status,
            "tenant_id": self.tenant_id,
            "resumable": self.resumable,
            "resume_hint": hint,
            "detail": self.detail,
        }


def parse_execution_handle_lookup(
    *,
    plan_approval_id: str | None = None,
    execution_id: str | None = None,
    task_id: str | None = None,
) -> tuple[ExecutionLayer, str] | str:
    """解析查询参数：三选一。成功返回 (layer, id)，失败返回错误信息。"""
    provided = [
        ("plan_approval", plan_approval_id),
        ("orchestrator", execution_id),
        ("long_run", task_id),
    ]
    active = [(layer, hid.strip()) for layer, hid in provided if hid and hid.strip()]
    if len(active) == 0:
        return "必须指定 plan_approval_id、execution_id 或 task_id 之一"
    if len(active) > 1:
        return "只能指定一种 handle：plan_approval_id / execution_id / task_id"
    layer_key, handle_id = active[0]
    layer_map: dict[str, ExecutionLayer] = {
        "plan_approval": "plan_approval",
        "orchestrator": "orchestrator",
        "long_run": "long_run",
    }
    return layer_map[layer_key], handle_id


def get_plan_approval_handle_status(
    plan_approval_id: str,
    *,
    tenant_id: str,
) -> ExecutionHandleStatus | None:
    from packages.agent.plan_approval import get_plan_approval

    entry = get_plan_approval(plan_approval_id)
    if entry is None:
        return None
    if entry.tenant_id != tenant_id:
        return None
    resumable = entry.status == "approved"
    hint = None
    if resumable:
        hint = ResumeHint(
            method="POST",
            path="/v1/agent/run",
            notes="body 含 plan_approval_id；需先 approve",
        )
    elif entry.status == "pending":
        hint = ResumeHint(
            method="POST",
            path=f"/v1/agent/plan/approval/{plan_approval_id}/approve",
            notes="审批通过后再 POST /v1/agent/run",
        )
    return ExecutionHandleStatus(
        layer="plan_approval",
        handle_id=plan_approval_id,
        status=entry.status,
        tenant_id=entry.tenant_id,
        resumable=resumable,
        resume_hint=hint,
        detail={
            "session_id": entry.session_id,
            "created_at": entry.created_at,
            "decided_at": entry.decided_at,
            "plan_goal": entry.plan.goal if entry.plan else None,
            "plan_steps_count": len(entry.plan.steps) if entry.plan else 0,
        },
    )


def get_orchestrator_handle_status(
    execution_id: str,
    *,
    tenant_id: str,
    checkpoint_store: GraphCheckpointStore,
) -> ExecutionHandleStatus | None:
    cp = checkpoint_store.get(execution_id)
    if cp is None:
        return None
    if cp.tenant_id != tenant_id:
        return None
    resumable = cp.status in _RESUMABLE_ORCHESTRATOR
    hint = None
    if resumable:
        hint = ResumeHint(
            method="POST",
            path=f"/internal/orchestrator/executions/{execution_id}/resume",
            notes="body 可含 inputs / max_steps；需 workflow 仍存在",
        )
    return ExecutionHandleStatus(
        layer="orchestrator",
        handle_id=execution_id,
        status=cp.status,
        tenant_id=cp.tenant_id,
        resumable=resumable,
        resume_hint=hint,
        detail={
            "workflow_id": cp.workflow_id,
            "current_node": cp.current_node,
            "error": cp.error,
            "created_at": cp.created_at,
            "updated_at": cp.updated_at,
            "trace_length": len(cp.trace),
        },
    )


async def get_long_run_handle_status(
    task_id: str,
    *,
    tenant_id: str,
) -> ExecutionHandleStatus | None:
    from packages.agent.long_horizon import get_task_status

    status_dict = await get_task_status(task_id)
    if status_dict is None:
        return None
    if status_dict.get("tenant_id") != tenant_id:
        return None
    task_status = str(status_dict.get("status", "unknown"))
    resumable = task_status in _RESUMABLE_LONG_RUN
    hint = None
    if resumable:
        hint = ResumeHint(
            method="POST",
            path=f"/v1/agent/long-run/{task_id}/resume",
            notes="触发 execute_long_run_resume → execute_plan(long_run_task_id=...)",
        )
    return ExecutionHandleStatus(
        layer="long_run",
        handle_id=task_id,
        status=task_status,
        tenant_id=str(status_dict.get("tenant_id", "")),
        resumable=resumable,
        resume_hint=hint,
        detail={
            "session_id": status_dict.get("session_id"),
            "progress": status_dict.get("progress"),
            "checkpoint_count": len(status_dict.get("checkpoints") or []),
            "step_states_count": len(status_dict.get("step_states") or []),
        },
    )


async def get_execution_handle_status(
    layer: ExecutionLayer,
    handle_id: str,
    *,
    tenant_id: str,
    checkpoint_store: GraphCheckpointStore | None = None,
) -> ExecutionHandleStatus | None:
    if layer == "plan_approval":
        return get_plan_approval_handle_status(handle_id, tenant_id=tenant_id)
    if layer == "orchestrator":
        if checkpoint_store is None:
            from packages.agent.graph_checkpoint import get_graph_checkpoint_store

            checkpoint_store = get_graph_checkpoint_store()
        return get_orchestrator_handle_status(
            handle_id,
            tenant_id=tenant_id,
            checkpoint_store=checkpoint_store,
        )
    if layer == "long_run":
        return await get_long_run_handle_status(handle_id, tenant_id=tenant_id)
    return None
