"""Orchestrator 带 checkpoint 的执行与 resume。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from packages.agent.graph_checkpoint import (
    GraphCheckpointStore,
    WorkflowExecutionCheckpoint,
    get_graph_checkpoint_store,
)
from packages.agent.orchestrator.engine import (
    ExecutionContext,
    ExecutionResult,
    OrchestratorError,
    traverse_workflow,
)
from packages.agent.orchestrator.graph import Workflow, validate_workflow

logger = logging.getLogger("ai_platform.orchestrator.checkpoint")


def _ctx_from_checkpoint(cp: WorkflowExecutionCheckpoint) -> ExecutionContext:
    return ExecutionContext(
        inputs=dict(cp.inputs),
        outputs=dict(cp.outputs),
        variables=dict(cp.variables),
        trace=list(cp.trace),
        current_node=cp.current_node,
    )


def _sync_checkpoint_from_ctx(
    cp: WorkflowExecutionCheckpoint,
    ctx: ExecutionContext,
    *,
    status: str,
    current_node: str | None,
    error: str | None = None,
) -> None:
    cp.inputs = dict(ctx.inputs)
    cp.outputs = dict(ctx.outputs)
    cp.variables = dict(ctx.variables)
    cp.trace = list(ctx.trace)
    cp.current_node = current_node
    cp.status = status  # type: ignore[assignment]
    cp.error = error


@dataclass
class _CheckpointPersister:
    cp: WorkflowExecutionCheckpoint
    store: GraphCheckpointStore

    async def after_advance(
        self,
        ctx: ExecutionContext,
        *,
        next_node: str,
        steps: int,
        start_time: float,
    ) -> None:
        _sync_checkpoint_from_ctx(self.cp, ctx, status="running", current_node=next_node)
        self.store.save(self.cp)

    async def on_workflow_completed(
        self,
        ctx: ExecutionContext,
        last_output: Any,
        *,
        workflow: Workflow,
        steps: int,
        start_time: float,
        execution_id: str | None,
    ) -> ExecutionResult | None:
        _sync_checkpoint_from_ctx(self.cp, ctx, status="completed", current_node=None)
        self.store.save(self.cp)
        elapsed_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            workflow_id=workflow.workflow_id,
            status="completed",
            outputs=ctx.outputs,
            final_output=last_output,
            trace=ctx.trace,
            execution_time_ms=elapsed_ms,
            execution_id=execution_id,
        )

    async def on_node_failure_persist(
        self,
        ctx: ExecutionContext,
        *,
        node_id: str,
        error: str,
        steps: int,
        start_time: float,
    ) -> None:
        _sync_checkpoint_from_ctx(
            self.cp,
            ctx,
            status="failed",
            current_node=node_id,
            error=error,
        )
        self.store.save(self.cp)

    async def after_error_redirect(
        self,
        ctx: ExecutionContext,
        *,
        next_node: str,
        steps: int,
        start_time: float,
    ) -> None:
        _sync_checkpoint_from_ctx(self.cp, ctx, status="running", current_node=next_node)
        self.store.save(self.cp)


async def execute_workflow_checkpointed(
    workflow: Workflow,
    *,
    tenant_id: str,
    inputs: dict[str, Any] | None = None,
    max_steps: int = 100,
    timeout_seconds: float = 300.0,
    checkpoint_store: GraphCheckpointStore | None = None,
    execution_id: str | None = None,
    resume: bool = False,
) -> ExecutionResult:
    """执行 workflow 并在每个节点后持久化 checkpoint（内存）。"""
    validate_workflow(workflow)
    store = checkpoint_store or get_graph_checkpoint_store()
    start_time = time.time()
    cp: WorkflowExecutionCheckpoint

    if resume:
        if not execution_id:
            raise OrchestratorError("INVALID_RESUME", "resume 需要 execution_id")
        loaded = store.get(execution_id)
        if loaded is None:
            raise OrchestratorError("CHECKPOINT_NOT_FOUND", f"execution_id 不存在: {execution_id}")
        if loaded.tenant_id != tenant_id:
            raise OrchestratorError("TENANT_MISMATCH", "execution 租户不匹配")
        if loaded.workflow_id != workflow.workflow_id:
            raise OrchestratorError("WORKFLOW_MISMATCH", "workflow_id 与 checkpoint 不一致")
        if loaded.status == "completed":
            raise OrchestratorError("ALREADY_COMPLETED", "该 execution 已完成")
        cp = loaded
        ctx = _ctx_from_checkpoint(cp)
        current = cp.current_node or workflow.start_node
        steps = len(ctx.trace)
    else:
        cp = store.create(
            tenant_id=tenant_id,
            workflow_id=workflow.workflow_id,
            inputs=inputs or {},
            start_node=workflow.start_node,
        )
        execution_id = cp.execution_id
        ctx = ExecutionContext(inputs=inputs or {})
        current = workflow.start_node
        steps = 0

    persister = _CheckpointPersister(cp=cp, store=store)

    try:
        outcome = await traverse_workflow(
            workflow,
            ctx,
            current=current,
            steps=steps,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            start_time=start_time,
            execution_id=execution_id,
            persister=persister,
        )
        if outcome.early_result is not None:
            return outcome.early_result

        elapsed_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            workflow_id=workflow.workflow_id,
            status="completed",
            outputs=outcome.ctx.outputs,
            final_output=outcome.last_output,
            trace=outcome.ctx.trace,
            execution_time_ms=elapsed_ms,
            execution_id=execution_id,
        )
    except OrchestratorError as exc:
        if exc.code == "MAX_STEPS":
            _sync_checkpoint_from_ctx(
                cp,
                ctx,
                status="failed",
                current_node=ctx.current_node,
                error=exc.message,
            )
            store.save(cp)
        elapsed_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            workflow_id=workflow.workflow_id,
            status="failed",
            outputs=ctx.outputs,
            final_output=None,
            trace=ctx.trace,
            error=exc.message,
            execution_time_ms=elapsed_ms,
            execution_id=execution_id,
        )
