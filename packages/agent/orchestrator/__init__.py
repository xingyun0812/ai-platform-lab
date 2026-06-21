"""控制流编排引擎 — Phase H #37

支持 DAG + 条件分支 + 循环 + 并行执行的 Agent 工作流引擎。

核心概念：
    GraphNode  — 节点（llm_call / tool_call / condition / parallel / loop / output）
    GraphEdge  — 边（带可选条件表达式）
    Workflow   — 图定义（nodes + edges + start_node + end_node）
    ExecutionContext — 运行时上下文（inputs / outputs / variables）

节点类型：
    start       — 入口，无操作
    end         — 出口，返回结果
    llm_call    — 调用 LLM
    tool_call   — 调用 Agent 工具
    condition   — 条件分支（if/elif/else）
    parallel    — 并行 fan-out + gather
    loop        — 循环（bounded + break condition）
    output      — 输出节点（格式化结果）

执行模型：
    1. 从 start_node 开始
    2. 拓扑遍历：执行节点 → 评估出边条件 → 选择下一节点
    3. 节点输出写入 ExecutionContext.outputs[node_id]
    4. 到达 end_node 返回

安全：
    - 循环最大迭代数限制（防死循环）
    - 并行最大分支数限制（防资源爆炸）
    - 条件表达式沙箱 eval（仅支持简单比较与布尔运算）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from packages.agent.orchestrator.engine import (
    ExecutionContext,
    ExecutionResult,
    OrchestratorError,
    execute_workflow,
)
from packages.agent.orchestrator.graph import (
    GraphEdge,
    GraphNode,
    Workflow,
    WorkflowValidationError,
    parse_workflow,
    validate_workflow,
)
from packages.agent.orchestrator.nodes import (
    ConditionBranch,
    LoopBody,
    ParallelBranch,
    register_node_executor,
)
from packages.agent.orchestrator.workflow_store import (
    WorkflowStore,
    get_workflow_store,
    init_workflow_store,
    reset_workflow_store_for_tests,
)

logger = logging.getLogger("ai_platform.orchestrator")

__all__ = [
    "ConditionBranch",
    "ExecutionContext",
    "ExecutionResult",
    "GraphEdge",
    "GraphNode",
    "LoopBody",
    "OrchestratorError",
    "ParallelBranch",
    "Workflow",
    "WorkflowStore",
    "WorkflowValidationError",
    "execute_workflow",
    "get_workflow_store",
    "init_workflow_store",
    "parse_workflow",
    "register_node_executor",
    "reset_workflow_store_for_tests",
    "validate_workflow",
]
