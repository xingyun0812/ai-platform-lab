"""packages/agent/plan_workflow.py — Phase Q Q5 Plan to workflow bridge.

Converts an AgentPlan (goal + steps + depends_on) into an Orchestrator-compatible
workflow spec (dict / YAML), aligning with the format in config/workflows/*.yaml.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.agent_schemas import AgentPlan

# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

_MAX_NAME_LEN = 50


def plan_to_workflow(plan: AgentPlan) -> dict[str, Any]:
    """将 AgentPlan 转换为 Orchestrator workflow spec。

    输出格式与 config/workflows/*.yaml 对齐：

    .. code-block:: yaml

        name: "<goal 前 50 字>"
        description: "<goal>"
        nodes:
          - id: "<step.id>"
            type: "agent"
            config:
              description: "<step.description>"
              tool_hint: "<step.tool_hint or null>"
              agent_hint: "<step.agent_hint or null>"
        edges:
          - from: "<dep_id>"
            to: "<step_id>"
        metadata:
          generated_by: plan_to_workflow
          plan_steps: <len(steps)>
          source: AgentPlan

    Args:
        plan: AgentPlan instance with goal and steps.

    Returns:
        A dict representing the workflow spec.
    """
    name = plan.goal[:_MAX_NAME_LEN]

    nodes: list[dict[str, Any]] = []
    for step in plan.steps:
        nodes.append(
            {
                "id": step.id,
                "type": "agent",
                "config": {
                    "description": step.description,
                    "tool_hint": step.tool_hint,
                    "agent_hint": step.agent_hint,
                },
            }
        )

    edges: list[dict[str, str]] = []
    for step in plan.steps:
        for dep_id in step.depends_on:
            edges.append({"from": dep_id, "to": step.id})

    return {
        "name": name,
        "description": plan.goal,
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated_by": "plan_to_workflow",
            "plan_steps": len(plan.steps),
            "source": "AgentPlan",
        },
    }


def workflow_to_yaml(workflow: dict[str, Any]) -> str:
    """将 workflow dict 序列化为 YAML 字符串。

    Args:
        workflow: Workflow spec dict (output of plan_to_workflow).

    Returns:
        YAML-formatted string.
    """
    import yaml  # PyYAML is already in project deps

    return yaml.dump(workflow, allow_unicode=True, default_flow_style=False, sort_keys=False)


def plan_to_workflow_yaml(plan: AgentPlan) -> str:
    """一步转换：AgentPlan → YAML 字符串。

    Convenience wrapper combining plan_to_workflow + workflow_to_yaml.

    Args:
        plan: AgentPlan instance.

    Returns:
        YAML string of the corresponding workflow spec.
    """
    return workflow_to_yaml(plan_to_workflow(plan))


def plan_to_orchestrator_workflow(
    plan: AgentPlan,
    *,
    workflow_id: str = "agent-plan",
) -> Any:
    """将 AgentPlan 转为可执行的 Orchestrator ``Workflow``。

    复用 planner 轨 ``plan_execution_layers``（Kahn BFS 依赖层分组）：无依赖、
    互斥(#246) 已在调度层裁决、资源冲突(#247) 由 resource_pool 运行时隔离的
    step 组装成 ``parallel`` 分支；强依赖 step 保持串行（前一层 gather 全部
    完成后才进入下一层）。单步层仍产出直接 ``plan_step`` 节点，线性 Plan 行为
    不变（向后兼容）。export 用的 ``plan_to_workflow`` 格式不变。

    Raises:
        ValueError: Plan 无 step 或存在循环依赖。
    """
    from packages.agent.orchestrator.graph import GraphEdge, GraphNode, Workflow, validate_workflow
    from packages.agent.planner import PlannerError, ordered_plan_steps, plan_execution_layers

    try:
        ordered = ordered_plan_steps(plan)
    except PlannerError as exc:
        raise ValueError(str(exc.message)) from exc
    if not ordered:
        raise ValueError("plan has no steps")

    reserved = {"start", "end"}
    for step in ordered:
        if step.id in reserved:
            raise ValueError(f"plan step id 不能与 orchestrator 保留节点冲突: {step.id}")

    total = len(ordered)
    step_index: dict[str, int] = {s.id: i for i, s in enumerate(ordered, start=1)}

    def _plan_step_config(step: Any) -> dict[str, Any]:
        return {
            "step_id": step.id,
            "description": step.description,
            "tool_hint": step.tool_hint,
            "agent_hint": step.agent_hint,
            "step_index": step_index[step.id],
            "step_total": total,
        }

    def _plan_step_node(step: Any) -> GraphNode:
        return GraphNode(
            node_id=step.id,
            node_type="plan_step",
            config=_plan_step_config(step),
            description=step.description,
        )

    def _branch_subgraph(step: Any) -> dict[str, Any]:
        """单步 parallel 分支子图：start → plan_step → end。"""
        return {
            "workflow_id": f"{workflow_id}__branch_{step.id}",
            "name": step.description[:_MAX_NAME_LEN],
            "nodes": [
                {"node_id": "start", "node_type": "start", "config": {}, "description": ""},
                _plan_step_node(step).to_dict(),
                {"node_id": "end", "node_type": "end", "config": {}, "description": ""},
            ],
            "edges": [
                {"from_node": "start", "to_node": step.id, "condition": None},
                {"from_node": step.id, "to_node": "end", "condition": None},
            ],
            "start_node": "start",
            "end_node": "end",
        }

    nodes: list[Any] = [GraphNode(node_id="start", node_type="start")]
    chain: list[str] = []  # 顶层线性链（plan_step 或 parallel 节点 id）

    for layer_index, layer in enumerate(plan_execution_layers(ordered), start=1):
        if len(layer) == 1:
            step = layer[0]
            nodes.append(_plan_step_node(step))
            chain.append(step.id)
            continue
        # 多步依赖层 → parallel 分支
        parallel_id = f"parallel_{layer_index}"
        branches = [{"id": step.id, "subgraph": _branch_subgraph(step)} for step in layer]
        nodes.append(
            GraphNode(
                node_id=parallel_id,
                node_type="parallel",
                config={
                    "branches": branches,
                    "gather": "all",
                    "max_concurrent": max(2, len(layer)),
                },
                description="parallel plan steps: " + ", ".join(s.id for s in layer),
            )
        )
        chain.append(parallel_id)

    nodes.append(GraphNode(node_id="end", node_type="end"))

    edges: list[Any] = [GraphEdge(from_node="start", to_node=chain[0])]
    for prev, nxt in zip(chain, chain[1:], strict=False):
        edges.append(GraphEdge(from_node=prev, to_node=nxt))
    edges.append(GraphEdge(from_node=chain[-1], to_node="end"))

    workflow = Workflow(
        workflow_id=workflow_id,
        name=plan.goal[:_MAX_NAME_LEN],
        nodes=nodes,
        edges=edges,
        start_node="start",
        end_node="end",
        description=plan.goal,
        metadata={
            "generated_by": "plan_to_orchestrator_workflow",
            "plan_steps": total,
            "layers": len(nodes) - 2,  # 除去 start/end 的顶层链节点数
            "parallel_layers": sum(1 for n in nodes if n.node_type == "parallel"),
            "source": "AgentPlan",
        },
    )
    validate_workflow(workflow)
    return workflow
