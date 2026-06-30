"""DAG 数据模型 — 节点 + 边 + 工作流定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class WorkflowValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class GraphNode:
    """图节点。

    node_type 取值：
        start | end | llm_call | tool_call | condition | parallel | loop | output

    config 按 node_type 不同：
        llm_call:  {"prompt": str, "model": str | None, "variables": dict}
        tool_call: {"tool_name": str, "arguments": dict}
        condition: {"branches": [{"condition": str, "target": str}, ...], "default": str}
        parallel:  {"branches": [{"id": str, "subgraph": Workflow}, ...], "gather": "all" | "first"}
        loop:      {"body": Workflow, "max_iterations": int, "break_condition": str | None}
        output:    {"value": str}  # value 支持 ${node_id.field} 模板
    """

    node_id: str
    node_type: str
    config: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "config": self.config,
            "description": self.description,
        }


@dataclass
class GraphEdge:
    """图边。condition 为 None 表示无条件（默认分支）。"""

    from_node: str
    to_node: str
    condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node": self.from_node,
            "to_node": self.to_node,
            "condition": self.condition,
        }


@dataclass
class Workflow:
    """工作流定义。"""

    workflow_id: str
    name: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    start_node: str
    end_node: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> GraphNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_out_edges(self, node_id: str) -> list[GraphEdge]:
        return [e for e in self.edges if e.from_node == node_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "start_node": self.start_node,
            "end_node": self.end_node,
            "description": self.description,
            "metadata": self.metadata,
        }


def validate_workflow(wf: Workflow) -> None:
    """校验 workflow 合法性。"""
    node_ids = {n.node_id for n in wf.nodes}
    # start/end 必须存在
    if wf.start_node not in node_ids:
        raise WorkflowValidationError(
            "START_NOT_FOUND", f"start_node {wf.start_node} 不存在"
        )
    if wf.end_node not in node_ids:
        raise WorkflowValidationError(
            "END_NOT_FOUND", f"end_node {wf.end_node} 不存在"
        )
    # start 节点类型必须为 start
    start_node = wf.get_node(wf.start_node)
    if start_node is None or start_node.node_type != "start":
        raise WorkflowValidationError(
            "INVALID_START", f"start_node {wf.start_node} 类型必须为 start"
        )
    end_node = wf.get_node(wf.end_node)
    if end_node is None or end_node.node_type != "end":
        raise WorkflowValidationError(
            "INVALID_END", f"end_node {wf.end_node} 类型必须为 end"
        )
    # 节点 ID 唯一
    if len(node_ids) != len(wf.nodes):
        raise WorkflowValidationError(
            "DUPLICATE_NODE_ID", "节点 ID 必须唯一"
        )
    # 边引用的节点必须存在
    for e in wf.edges:
        if e.from_node not in node_ids:
            raise WorkflowValidationError(
                "EDGE_INVALID", f"边 from_node {e.from_node} 不存在"
            )
        if e.to_node not in node_ids:
            raise WorkflowValidationError(
                "EDGE_INVALID", f"边 to_node {e.to_node} 不存在"
            )
    # 节点类型校验
    for n in wf.nodes:
        if n.node_type not in (
            "start", "end", "llm_call", "tool_call",
            "condition", "parallel", "loop", "output", "agent_call", "plan_step",
        ):
            raise WorkflowValidationError(
                "INVALID_NODE_TYPE", f"节点 {n.node_id} 类型 {n.node_type} 未知"
            )
        # condition 节点必须有 branches
        if n.node_type == "condition":
            branches = n.config.get("branches")
            if not isinstance(branches, list) or not branches:
                raise WorkflowValidationError(
                    "INVALID_CONDITION",
                    f"condition 节点 {n.node_id} 缺少 branches",
                )
        # loop 节点必须有 body + max_iterations
        if n.node_type == "loop":
            if not isinstance(n.config.get("body"), dict):
                raise WorkflowValidationError(
                    "INVALID_LOOP",
                    f"loop 节点 {n.node_id} 缺少 body",
                )
            max_iter = n.config.get("max_iterations", 0)
            if not isinstance(max_iter, int) or max_iter <= 0:
                raise WorkflowValidationError(
                    "INVALID_LOOP",
                    f"loop 节点 {n.node_id} max_iterations 必须 > 0",
                )
        # agent_call 节点必须有 agent_id
        if n.node_type == "agent_call":
            if not n.config.get("agent_id"):
                raise WorkflowValidationError(
                    "INVALID_AGENT_CALL",
                    f"agent_call 节点 {n.node_id} 缺少 agent_id",
                )
        # plan_step 节点必须有 description
        if n.node_type == "plan_step":
            if not str(n.config.get("description", "")).strip():
                raise WorkflowValidationError(
                    "INVALID_PLAN_STEP",
                    f"plan_step 节点 {n.node_id} 缺少 description",
                )
        # parallel 节点必须有 branches
        if n.node_type == "parallel":
            branches = n.config.get("branches")
            if not isinstance(branches, list) or not branches:
                raise WorkflowValidationError(
                    "INVALID_PARALLEL",
                    f"parallel 节点 {n.node_id} 缺少 branches",
                )


def parse_workflow(data: dict[str, Any]) -> Workflow:
    """从 dict 解析 Workflow（支持嵌套 subgraph）。"""
    nodes = [
        GraphNode(
            node_id=str(n["node_id"]),
            node_type=str(n["node_type"]),
            config=dict(n.get("config", {})),
            description=str(n.get("description", "")),
        )
        for n in data.get("nodes", [])
        if isinstance(n, dict) and "node_id" in n and "node_type" in n
    ]
    edges = [
        GraphEdge(
            from_node=str(e["from_node"]),
            to_node=str(e["to_node"]),
            condition=e.get("condition"),
        )
        for e in data.get("edges", [])
        if isinstance(e, dict) and "from_node" in e and "to_node" in e
    ]
    wf = Workflow(
        workflow_id=str(data["workflow_id"]),
        name=str(data.get("name", data["workflow_id"])),
        nodes=nodes,
        edges=edges,
        start_node=str(data["start_node"]),
        end_node=str(data["end_node"]),
        description=str(data.get("description", "")),
        metadata=dict(data.get("metadata", {})),
    )
    validate_workflow(wf)
    return wf
