"""Agent 生命周期管理包 — Phase H #39

提供 AgentVersion 版本化、蓝绿发布、金丝雀灰度、回滚能力。
"""

from __future__ import annotations

from packages.agent.lifecycle.registry import (
    AgentLifecycleRegistry,
    AgentVersion,
    RolloutStatus,
    RolloutStrategy,
    get_lifecycle_registry,
    init_lifecycle_registry,
    reset_lifecycle_registry_for_tests,
)

__all__ = [
    "AgentVersion",
    "AgentLifecycleRegistry",
    "init_lifecycle_registry",
    "get_lifecycle_registry",
    "reset_lifecycle_registry_for_tests",
    "RolloutStrategy",
    "RolloutStatus",
]
