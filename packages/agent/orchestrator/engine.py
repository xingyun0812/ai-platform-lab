"""执行引擎 — 拓扑遍历 + 条件跳转 + 状态传递。"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from packages.agent.orchestrator.graph import (
    GraphNode,
    Workflow,
    parse_workflow,
    validate_workflow,
)
from packages.agent.orchestrator.nodes import (
    NodeExecutorError,
    evaluate_condition,
    get_executor,
    render_template,
)

logger = logging.getLogger("ai_platform.orchestrator.engine")


class OrchestratorError(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


@dataclass
class ExecutionContext:
    """运行时上下文。"""
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)  # node_id → output
    variables: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)  # 执行轨迹
    started_at: float = field(default_factory=time.time)
    current_node: str | None = None

    def record_trace(self, node_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
        self.trace.append({
            "node_id": node_id,
            "status": status,
            "timestamp": time.time(),
            "detail": detail or {},
        })


@dataclass
class ExecutionResult:
    """工作流执行结果。"""
    workflow_id: str
    status: str  # completed | failed | timeout
    outputs: dict[str, Any]
    final_output: Any
    trace: list[dict[str, Any]]
    error: str | None = None
    execution_time_ms: float = 0.0


async def execute_workflow(
    workflow: Workflow,
    *,
    inputs: dict[str, Any] | None = None,
    max_steps: int = 100,
    timeout_seconds: float = 300.0,
) -> ExecutionResult:
    """执行工作流。

    Args:
        workflow: 已校验的 Workflow 对象
        inputs: 输入变量
        max_steps: 最大节点执行数（防死循环）
        timeout_seconds: 总超时

    Returns:
        ExecutionResult
    """
    validate_workflow(workflow)
    ctx = ExecutionContext(inputs=inputs or {})
    start_time = time.time()
    current = workflow.start_node
    steps = 0
    last_output: Any = None
    error: str | None = None

    try:
        while current and steps < max_steps:
            if time.time() - start_time > timeout_seconds:
                raise OrchestratorError("TIMEOUT", f"执行超时 {timeout_seconds}s")
            ctx.current_node = current
            node = workflow.get_node(current)
            if node is None:
                raise OrchestratorError(
                    "NODE_NOT_FOUND", f"节点 {current} 不存在"
                )
            steps += 1
            logger.debug(
                "orchestrator executing node=%s type=%s step=%d",
                current, node.node_type, steps,
            )
            # 执行节点
            try:
                executor = get_executor(node.node_type)
                if executor is None:
                    raise OrchestratorError(
                        "NO_EXECUTOR", f"节点类型 {node.node_type} 无执行器"
                    )
                output = await executor(node.config, ctx)
                ctx.outputs[current] = output
                ctx.record_trace(current, "completed", {"output": _summarize(output)})
                if node.node_type == "end":
                    last_output = output
                    break
            except NodeExecutorError as e:
                ctx.record_trace(current, "failed", {"error": e.message})
                error = e.message
                # 查找 error 边
                error_target = _find_error_target(workflow, current)
                if error_target:
                    current = error_target
                    continue
                raise OrchestratorError("NODE_FAILED", e.message, {"node": current})
            # 决定下一节点
            next_node = _select_next_node(workflow, node, ctx)
            if next_node is None:
                raise OrchestratorError(
                    "NO_NEXT_NODE", f"节点 {current} 无可用出边"
                )
            current = next_node
        if steps >= max_steps:
            raise OrchestratorError(
                "MAX_STEPS", f"超过最大步数 {max_steps}"
            )
        elapsed_ms = (time.time() - start_time) * 1000
        return ExecutionResult(
            workflow_id=workflow.workflow_id,
            status="completed",
            outputs=ctx.outputs,
            final_output=last_output,
            trace=ctx.trace,
            execution_time_ms=elapsed_ms,
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
        )


async def execute_subgraph(
    subgraph_data: dict[str, Any],
    parent_ctx: ExecutionContext,
    branch_id: str,
) -> dict[str, Any]:
    """执行子图（用于 parallel / loop 节点）。

    共享 parent_ctx 的 inputs/variables，但独立的 outputs。
    """
    try:
        sub_wf = parse_workflow(subgraph_data)
    except Exception as e:
        return {"error": f"subgraph parse failed: {e}"}
    # 共享上下文
    sub_ctx = ExecutionContext(
        inputs=parent_ctx.inputs,
        variables=parent_ctx.variables,
        outputs={**parent_ctx.outputs},  # 继承父上下文输出
    )
    current = sub_wf.start_node
    steps = 0
    while current and steps < 50:  # 子图限制 50 步
        sub_ctx.current_node = current
        node = sub_wf.get_node(current)
        if node is None:
            break
        steps += 1
        try:
            executor = get_executor(node.node_type)
            if executor is None:
                break
            output = await executor(node.config, sub_ctx)
            sub_ctx.outputs[current] = output
            if node.node_type == "end":
                # 回写父上下文
                parent_ctx.outputs[f"{branch_id}_result"] = output
                return {"branch_id": branch_id, "output": output, "status": "completed"}
        except NodeExecutorError as e:
            return {"branch_id": branch_id, "error": e.message, "status": "failed"}
        next_node = _select_next_node(sub_wf, node, sub_ctx)
        if next_node is None:
            break
        current = next_node
    return {"branch_id": branch_id, "status": "no_end", "outputs": sub_ctx.outputs}


def _select_next_node(
    workflow: Workflow,
    node: GraphNode,
    ctx: ExecutionContext,
) -> str | None:
    """选择下一节点。

    - condition 节点：读取 output.branch 直接跳转（无需显式边）
    - 其他节点：评估出边条件，选第一个匹配
    """
    # condition 节点优先：从 output.branch 直接跳转
    if node.node_type == "condition":
        output = ctx.outputs.get(node.node_id, {})
        target = output.get("branch") if isinstance(output, dict) else None
        if target:
            return target
        # 回退到第一条出边
        out_edges = workflow.get_out_edges(node.node_id)
        return out_edges[0].to_node if out_edges else None
    # 普通节点：找第一条无条件边或条件为真的边
    out_edges = workflow.get_out_edges(node.node_id)
    if not out_edges:
        return None
    for edge in out_edges:
        if edge.condition is None:
            return edge.to_node
        if evaluate_condition(edge.condition, ctx):
            return edge.to_node
    # 全部有条件但不匹配：回退第一条
    return out_edges[0].to_node if out_edges else None


def _find_error_target(workflow: Workflow, node_id: str) -> str | None:
    """查找 error 边（condition == "error"）。"""
    for edge in workflow.get_out_edges(node_id):
        if edge.condition == "error":
            return edge.to_node
    return None


def _summarize(value: Any, max_len: int = 200) -> str:
    """截断输出用于 trace。"""
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "...[truncated]"
    return s
