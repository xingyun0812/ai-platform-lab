"""Multi-Agent 协作框架 — Phase H #38

基于 #37 控制流编排引擎，实现主 Agent 委托子 Agent 协作。

核心概念：
    AgentSpec    — Agent 定义（角色 + 工具集 + 模型 + system prompt）
    AgentRegistry — Agent 注册表
    Delegation   — 主 Agent 委托子 Agent 执行任务
    Communication — Agent 间消息传递（共享黑板 + 直接通信）

协作模式：
    1. **委托**：主 Agent 调用 `delegate_to(agent_id, task)` 让子 Agent 执行任务
    2. **并行委托**：主 Agent 同时委托多个子 Agent，聚合结果
    3. **链式**：Agent A → Agent B → Agent C 顺序处理
    4. **监督**：主 Agent 监督多个子 Agent 并裁决结果

集成点：
    - orchestrator 新增 `agent_call` 节点类型
    - REST API 管理 AgentSpec
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from packages.agent.multi_agent.delegation import (
    DelegationError,
    DelegationResult,
    delegate_to_agent,
)
from packages.agent.multi_agent.registry import (
    AgentRegistry,
    AgentRegistryError,
    AgentSpec,
    AgentStatus,
    get_agent_registry,
    init_agent_registry,
    reset_agent_registry_for_tests,
)

logger = logging.getLogger("ai_platform.multi_agent")

__all__ = [
    "AgentRegistry",
    "AgentRegistryError",
    "AgentSpec",
    "AgentStatus",
    "DelegationError",
    "DelegationResult",
    "delegate_to_agent",
    "get_agent_registry",
    "init_agent_registry",
    "reset_agent_registry_for_tests",
]
