"""Orchestrator 带 checkpoint 的执行与 resume。"""

from __future__ import annotations

import logging
import time
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
    _find_error_target,
    _select_next_node,
    _summarize,
)
from packages.agent.orchestrator.graph import Workflow, validate_workflow
from packages.agent.orchestrator.nodes import NodeExecutorError, get_executor

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

    last_output: Any = None

    try:
        while current and steps < max_steps:
            if time.time() - start_time > timeout_seconds:
                raise OrchestratorError("TIMEOUT", f"执行超时 {timeout_seconds}s")
            ctx.current_node = current
            node = workflow.get_node(current)
            if node is None:
                raise OrchestratorError("NODE_NOT_FOUND", f"节点 {current} 不存在")
            steps += 1
            try:
                executor = get_executor(node.node_type)
                if executor is None:
                    raise OrchestratorError("NO_EXECUTOR", f"节点类型 {node.node_type} 无执行器")
                output = await executor(node.config, ctx)
                ctx.outputs[current] = output
                ctx.record_trace(current, "completed", {"output": _summarize(output)})
                if node.node_type == "end":
                    last_output = output
                    _sync_checkpoint_from_ctx(cp, ctx, status="completed", current_node=None)
                    store.save(cp)
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
            except NodeExecutorError as e:
                ctx.record_trace(current, "failed", {"error": e.message})
                error_target = _find_error_target(workflow, current)
                if error_target:
                    current = error_target
                    _sync_checkpoint_from_ctx(cp, ctx, status="running", current_node=current)
                    store.save(cp)
                    continue
                _sync_checkpoint_from_ctx(
                    cp, ctx, status="failed", current_node=current, error=e.message
                )
                store.save(cp)
                raise OrchestratorError("NODE_FAILED", e.message, {"node": current})

            next_node = _select_next_node(workflow, node, ctx)
            if next_node is None:
                raise OrchestratorError("NO_NEXT_NODE", f"节点 {current} 无可用出边")
            current = next_node
            _sync_checkpoint_from_ctx(cp, ctx, status="running", current_node=current)
            store.save(cp)

        if steps >= max_steps:
            _sync_checkpoint_from_ctx(cp, ctx, status="failed", current_node=current, error="MAX_STEPS")
            store.save(cp)
            raise OrchestratorError("MAX_STEPS", f"超过最大步数 {max_steps}")

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
    except OrchestratorError as e:
        elapsed_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            workflow_id=workflow.workflow_id,
            status="failed",
            outputs=ctx.outputs,
            final_output=None,
            trace=ctx.trace,
            error=e.message,
            execution_time_ms=elapsed_ms,
            execution_id=execution_id,
        )
