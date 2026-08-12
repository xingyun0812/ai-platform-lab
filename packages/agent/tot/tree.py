from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ThoughtNode:
    """ToT 思维树中的一个节点。

    每个节点代表一个推理中间状态（文本），
    由 Generator 生成、Evaluator 评分、Searcher 选择。
    """

    node_id: str
    state: str  # 当前推理文本
    value: float | None = None  # Evaluator 评分（越高越好）
    status: str = "pending"  # pending | sure | maybe | impossible
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    depth: int = 0
    visits: int = 0  # 预留 MCTS 扩展
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state,
            "value": self.value,
            "status": self.status,
            "parent_id": self.parent_id,
            "children": self.children,
            "depth": self.depth,
            "visits": self.visits,
            "metadata": self.metadata,
        }


@dataclass
class TotConfig:
    """ToT 搜索策略配置。"""

    enabled: bool = False
    search_algorithm: str = "bfs"  # bfs | dfs
    branching_factor: int = 3  # 每个节点生成几个候选
    beam_width: int = 2  # BFS 每层保留几个节点
    max_depth: int = 5  # 最大搜索深度
    max_total_nodes: int = 50  # 总节点数上限（安全阀）
    value_threshold: float | None = None  # DFS 剪枝阈值
    temperature: float = 0.7  # 生成时的温度
    timeout_seconds: float = 120.0
    # MCTS 参数
    mcts_exploration_weight: float = 1.4  # UCB 探索常数 C
    mcts_simulations: int = 5  # 每次选代模拟次数

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "search_algorithm": self.search_algorithm,
            "branching_factor": self.branching_factor,
            "beam_width": self.beam_width,
            "max_depth": self.max_depth,
            "max_total_nodes": self.max_total_nodes,
            "value_threshold": self.value_threshold,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class CandidateThought:
    """候选思维（Generator 输出 → Evaluator 输入）。"""

    text: str
    value: float | None = None
    status: str = "pending"  # pending | sure | maybe | impossible
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThoughtTree:
    """整棵思维树。"""

    root_id: str
    nodes: dict[str, ThoughtNode]
    goal: str
    config: TotConfig
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, goal: str, initial_state: str, config: TotConfig | None = None) -> ThoughtTree:
        resolved = config or TotConfig()
        root = ThoughtNode(
            node_id=_gen_id(),
            state=initial_state,
            depth=0,
        )
        return cls(
            root_id=root.node_id,
            nodes={root.node_id: root},
            goal=goal,
            config=resolved,
        )

    def get_node(self, node_id: str) -> ThoughtNode | None:
        return self.nodes.get(node_id)

    def add_node(self, parent_id: str, state: str, **kwargs: Any) -> ThoughtNode:
        parent = self.get_node(parent_id)
        if parent is None:
            raise ValueError(f"parent_id {parent_id} not found")
        child = ThoughtNode(
            node_id=_gen_id(),
            state=state,
            parent_id=parent_id,
            depth=parent.depth + 1,
            **kwargs,
        )
        self.nodes[child.node_id] = child
        parent.children.append(child.node_id)
        return child

    def leaf_nodes(self) -> list[ThoughtNode]:
        """返回所有没有子节点的节点（叶节点）。"""
        return [n for n in self.nodes.values() if not n.children]

    def active_leaves(self) -> list[ThoughtNode]:
        """返回状态为 pending 或 maybe 的叶节点（可继续扩展）。"""
        return [
            n for n in self.leaf_nodes()
            if n.status in ("pending", "maybe")
        ]

    def best_path(self) -> list[ThoughtNode]:
        """从根到最优叶节点的路径。

        按 value 降序排列叶节点，选取最优的叶节点，
        沿 parent_id 回溯到根，返回路径列表。
        """
        leaves = self.leaf_nodes()
        if not leaves:
            return []
        scored = [(n.value or -float("inf"), n) for n in leaves]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_leaf = scored[0][1]
        path: list[ThoughtNode] = []
        current: ThoughtNode | None = best_leaf
        while current is not None:
            path.append(current)
            current = self.get_node(current.parent_id) if current.parent_id else None
        path.reverse()
        return path

    def best_answer(self) -> str | None:
        """返回最优路径上最后一个节点的 state。"""
        path = self.best_path()
        if not path:
            return None
        return path[-1].state

    def total_nodes(self) -> int:
        return len(self.nodes)

    def max_depth_reached(self) -> int:
        if not self.nodes:
            return 0
        return max(n.depth for n in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "goal": self.goal,
            "config": self.config.to_dict(),
            "total_nodes": self.total_nodes(),
            "max_depth": self.max_depth_reached(),
        }


@dataclass
class TotResult:
    """ToT 搜索结果。"""

    tree: ThoughtTree
    best_answer: str | None
    best_value: float
    total_nodes: int
    search_depth: int
    execution_time_ms: float
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_answer": self.best_answer,
            "best_value": self.best_value,
            "total_nodes": self.total_nodes,
            "search_depth": self.search_depth,
            "execution_time_ms": self.execution_time_ms,
            "trace": self.trace,
            "error": self.error,
            "tree": self.tree.to_dict(),
        }


def _gen_id() -> str:
    return uuid.uuid4().hex[:12]
