"""执行引擎 — 拓扑遍历 + 条件跳转 + 状态传递。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

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
        self.trace.append(
            {
                "node_id": node_id,
                "status": status,
                "timestamp": time.time(),
                "detail": detail or {},
            }
        )


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
    execution_id: str | None = None


@dataclass
class TraversalComplete:
    ctx: ExecutionContext
    last_output: Any
    steps: int
    early_result: ExecutionResult | None = None


@runtime_checkable
class WorkflowTraversalPersister(Protocol):
    """节点步进间可选持久化（checkpoint 轨）。"""

    async def after_advance(
        self,
        ctx: ExecutionContext,
        *,
        next_node: str,
        steps: int,
        start_time: float,
    ) -> None: ...

    async def on_workflow_completed(
        self,
        ctx: ExecutionContext,
        last_output: Any,
        *,
        workflow: Workflow,
        steps: int,
        start_time: float,
        execution_id: str | None,
    ) -> ExecutionResult | None: ...

    async def on_node_failure_persist(
        self,
        ctx: ExecutionContext,
        *,
        node_id: str,
        error: str,
        steps: int,
        start_time: float,
    ) -> None: ...

    async def after_error_redirect(
        self,
        ctx: ExecutionContext,
        *,
        next_node: str,
        steps: int,
        start_time: float,
    ) -> None: ...


async def traverse_workflow(
    workflow: Workflow,
    ctx: ExecutionContext,
    *,
    current: str,
    steps: int,
    max_steps: int,
    timeout_seconds: float,
    start_time: float,
    execution_id: str | None = None,
    persister: WorkflowTraversalPersister | None = None,
) -> TraversalComplete:
    """拓扑遍历 workflow，直至 end / 失败 / 超步数。"""
    last_output: Any = None

    while current and steps < max_steps:
        if time.time() - start_time > timeout_seconds:
            raise OrchestratorError("TIMEOUT", f"执行超时 {timeout_seconds}s")

        ctx.current_node = current
        node = workflow.get_node(current)
        if node is None:
            raise OrchestratorError("NODE_NOT_FOUND", f"节点 {current} 不存在")

        steps += 1
        logger.debug(
            "orchestrator executing node=%s type=%s step=%d",
            current,
            node.node_type,
            steps,
        )
        try:
            executor = get_executor(node.node_type)
            if executor is None:
                raise OrchestratorError("NO_EXECUTOR", f"节点类型 {node.node_type} 无执行器")
            output = await executor(node.config, ctx)
            # 聚合校验钩子（#248）：parallel/tool_call 节点执行后按 output_schema
            # 校验产物完整性，标记失败/缺失产物到 trace，不阻塞成功分支。
            agg = await _run_aggregation_hook(node.node_type, node.config, output)
            ctx.outputs[current] = output
            ctx.record_trace(
                current,
                "completed",
                {"output": _summarize(output), "aggregation": agg.get("aggregation")},
            )

            if node.node_type == "end":
                last_output = output
                if persister is not None:
                    early = await persister.on_workflow_completed(
                        ctx,
                        last_output,
                        workflow=workflow,
                        steps=steps,
                        start_time=start_time,
                        execution_id=execution_id,
                    )
                    if early is not None:
                        return TraversalComplete(
                            ctx=ctx,
                            last_output=last_output,
                            steps=steps,
                            early_result=early,
                        )
                break

        except NodeExecutorError as exc:
            ctx.record_trace(current, "failed", {"error": exc.message})
            error_target = _find_error_target(workflow, current)
            if error_target:
                current = error_target
                if persister is not None:
                    await persister.after_error_redirect(
                        ctx,
                        next_node=current,
                        steps=steps,
                        start_time=start_time,
                    )
                continue
            if persister is not None:
                await persister.on_node_failure_persist(
                    ctx,
                    node_id=current,
                    error=exc.message,
                    steps=steps,
                    start_time=start_time,
                )
            raise OrchestratorError("NODE_FAILED", exc.message, {"node": current}) from exc

        next_node = _select_next_node(workflow, node, ctx)
        if next_node is None:
            raise OrchestratorError("NO_NEXT_NODE", f"节点 {current} 无可用出边")
        current = next_node
        if persister is not None:
            await persister.after_advance(
                ctx,
                next_node=current,
                steps=steps,
                start_time=start_time,
            )

    if steps >= max_steps:
        raise OrchestratorError("MAX_STEPS", f"超过最大步数 {max_steps}")

    return TraversalComplete(ctx=ctx, last_output=last_output, steps=steps)


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

    try:
        outcome = await traverse_workflow(
            workflow,
            ctx,
            current=workflow.start_node,
            steps=0,
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
            start_time=start_time,
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
            if node.node_type == "plan_step":
                # #249: 并行/loop 分支内的 plan_step 产物回写父上下文，键 = step.id。
                # 使 execution_engine 的 outputs[step.id] 映射（_map_workflow_result_to_plan_dict）
                # 对 parallel 分支内 step 同样成立，与顶层 plan_step 产出兼容。
                step_id = str(node.config.get("step_id") or "")
                if step_id:
                    parent_ctx.outputs[step_id] = output
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


async def _run_aggregation_hook(
    node_type: str, config: dict[str, Any], output: Any
) -> dict[str, Any]:
    """聚合校验钩子：parallel/tool_call 节点执行后，按各工具声明的 output_schema
    校验产物完整性（缺失/失败识别），对缺产物重跑工具补全，标记仍失败产物到
    trace，不阻塞成功分支。

    - 仅对声明了 ``output_schema`` 的工具做校验/聚合补全。
    - 未声明 schema 的工具：不校验、不补全、按原样透传（向后兼容）。
    - 补全次数来自 ``config/agent.yaml`` 的 ``aggregation_completion_attempts``
      （缺省 1），可通过补全回调可配置。
    - 从不抛出异常，返回 ``{\"aggregation\": ...}`` 供 trace 落库。
    """
    from packages.agent.registry import ToolRegistry
    from packages.agent.scheduling.aggregator import aggregate_tool_outputs
    from packages.agent.scheduling.schedule_policy import SchedulePolicyStore

    products: list[dict[str, Any]] = []
    schemas: dict[str, Any] = {}
    store = SchedulePolicyStore()
    original_args: dict[str, Any] = {}

    if node_type == "tool_call":
        tool_name = str(config.get("tool_name", ""))
        if not tool_name:
            return {"aggregation": {"status": "skipped", "reason": "no_tool_name"}}
        products.append({"tool_name": tool_name, "result": output})
        schemas[tool_name] = store.resolve(tool_name).output_schema
        original_args[tool_name] = (
            config.get("arguments", {}) if isinstance(config.get("arguments"), dict) else {}
        )
    elif node_type == "parallel":
        branch_results = output.get("results", []) if isinstance(output, dict) else []
        for br in branch_results:
            if not isinstance(br, dict):
                continue
            name = str(br.get("branch_id") or br.get("tool_name") or "")
            if not name:
                continue
            products.append({"tool_name": name, "result": br.get("output")})
            schemas[name] = store.resolve(name).output_schema
            # 平行分支子图已执行完，原始 arguments 不可得 → 补全以空参 + 缺失提示重跑
            original_args[name] = {}
    else:
        return {"aggregation": {"status": "skipped", "reason": "not_aggregated_type"}}

    if not products:
        return {"aggregation": {"status": "skipped", "reason": "no_products"}}

    # 补全回调（#248 AC2）：缺失/失败产物用 ToolRegistry 重跑该工具，按原
    # arguments（tool_call 节点）或空参（parallel 分支）重跑；返回与节点输出
    # 一致的 wrapper dict，使 schema 校验一致。异常/无工具 → None（补全失败）。
    async def _completion_cb(tool_name: str, old_output: Any) -> Any:
        registry = ToolRegistry()
        tool = registry.get(tool_name)
        if tool is None:
            return None
        args: dict[str, Any] = dict(original_args.get(tool_name) or {})
        try:
            new_result: Any = await tool.handler(args)
        except Exception:  # noqa: BLE001 — 补全执行失败视为本次补全未成功
            return None
        return {"result": new_result, "tool": tool_name}

    completion_attempts = _aggregation_completion_attempts()

    result = await aggregate_tool_outputs(
        products,
        schemas=schemas,
        completions_cb=_completion_cb,
        completion_attempts=completion_attempts,
    )
    if not result.has_failures:
        return {"aggregation": {"status": "ok", "errors": [], "attempts": result.attempts}}
    failed = [
        {
            "tool_name": p.tool_name,
            "status": p.status,
            "missing_fields": list(p.missing_fields),
            "attempts": p.attempts,
        }
        for p in result.failed
    ]
    return {
        "aggregation": {
            "status": "partial",
            "errors": list(result.errors),
            "failed": failed,
            "attempts": result.attempts,
        }
    }


def _aggregation_completion_attempts() -> int:
    """读取补全重试次数：config/agent.yaml 的 aggregation_completion_attempts，缺省 1。

    轻量读取，不新增 Platform Protocol 字段；yaml 缺失/解析失败回退默认值。
    """
    from pathlib import Path

    try:
        import yaml

        path = Path(__file__).resolve().parents[3] / "config" / "agent.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        value = raw.get("aggregation_completion_attempts", 1)
        return max(0, int(value)) if isinstance(value, int) else 1
    except Exception:  # noqa: BLE001 — 配置读取失败回退默认 1
        return 1
