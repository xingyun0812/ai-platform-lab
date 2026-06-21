"""AgentSpec 数据模型 + 注册表。"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ai_platform.multi_agent.registry")


class AgentRegistryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class AgentSpec:
    """Agent 定义。

    Agent 类型：
        primary   — 主 Agent（用户直接交互）
        specialist — 专家 Agent（RAG / 代码 / 翻译等领域）
        reviewer  — 审核 Agent（监督其他 Agent 输出）
        router    — 路由 Agent（分发任务到其他 Agent）
    """

    agent_id: str
    name: str
    role: str = "specialist"  # primary | specialist | reviewer | router
    description: str = ""
    system_prompt: str = ""
    model: str | None = None  # None → 用 default_model
    allowed_tools: list[str] = field(default_factory=list)
    # 委托限制
    can_delegate: bool = False  # 是否允许委托其他 Agent
    can_be_delegated_to: bool = True  # 是否可被其他 Agent 委托
    max_delegation_depth: int = 3  # 最大委托深度（防递归爆炸）
    # 元数据
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    created_by: str = "system"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """工具白名单检查。空列表 = 允许所有工具。"""
        if not self.allowed_tools:
            return True
        return tool_name in self.allowed_tools


@dataclass
class AgentStatus:
    """Agent 运行时状态。"""
    healthy: bool = True
    last_invoked: float = 0.0
    invocation_count: int = 0
    last_error: str = ""


class AgentRegistry:
    """Agent 注册表。

    存储：
        config/agents.yaml — 默认配置（git 跟踪）
        data/agents_overrides.json — admin API 运行时修改（不进 git）
    """

    def __init__(
        self,
        *,
        yaml_path: Path | None = None,
        overrides_path: Path | None = None,
    ) -> None:
        self._yaml_path = yaml_path
        self._overrides_path = overrides_path
        self._lock = threading.RLock()
        self._agents: dict[str, AgentSpec] = {}
        self._statuses: dict[str, AgentStatus] = {}
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            self._agents.clear()
            self._statuses.clear()
            if self._yaml_path and self._yaml_path.is_file():
                try:
                    data = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
                    self._merge_yaml(data)
                    logger.info(
                        "agent registry loaded yaml=%s agents=%d",
                        self._yaml_path,
                        len(self._agents),
                    )
                except Exception as e:
                    logger.warning("agent yaml load failed: %s", e)
            if self._overrides_path and self._overrides_path.is_file():
                try:
                    data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
                    self._merge_overrides(data)
                except Exception as e:
                    logger.warning("agent overrides load failed: %s", e)
            self._loaded = True

    def _merge_yaml(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        agents = data.get("agents")
        if not isinstance(agents, list):
            return
        for item in agents:
            spec = self._parse_spec(item)
            if spec is not None:
                self._agents[spec.agent_id] = spec
                self._statuses[spec.agent_id] = AgentStatus()

    def _merge_overrides(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        agents = data.get("agents")
        if not isinstance(agents, list):
            return
        for item in agents:
            spec = self._parse_spec(item)
            if spec is not None:
                self._agents[spec.agent_id] = spec
                self._statuses.setdefault(spec.agent_id, AgentStatus())

    def _parse_spec(self, item: dict[str, Any]) -> AgentSpec | None:
        try:
            agent_id = item.get("agent_id")
            if not agent_id or not isinstance(agent_id, str):
                logger.warning("agent spec parse skipped: missing agent_id item=%r", item)
                return None
            return AgentSpec(
                agent_id=str(agent_id),
                name=str(item.get("name", agent_id)),
                role=str(item.get("role", "specialist")),
                description=str(item.get("description", "")),
                system_prompt=str(item.get("system_prompt", "")),
                model=item.get("model"),
                allowed_tools=list(item.get("allowed_tools", [])),
                can_delegate=bool(item.get("can_delegate", False)),
                can_be_delegated_to=bool(item.get("can_be_delegated_to", True)),
                max_delegation_depth=int(item.get("max_delegation_depth", 3)),
                enabled=bool(item.get("enabled", True)),
                created_at=float(item.get("created_at", time.time())),
                created_by=str(item.get("created_by", "system")),
                metadata=dict(item.get("metadata", {})),
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("agent spec parse failed: %s item=%r", e, item)
            return None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def list_agents(self) -> list[AgentSpec]:
        self._ensure_loaded()
        with self._lock:
            return [self._agents[aid] for aid in sorted(self._agents.keys())]

    def list_agent_ids(self) -> list[str]:
        self._ensure_loaded()
        with self._lock:
            return sorted(self._agents.keys())

    def get_agent(self, agent_id: str) -> AgentSpec | None:
        self._ensure_loaded()
        with self._lock:
            return self._agents.get(agent_id)

    def get_status(self, agent_id: str) -> AgentStatus | None:
        self._ensure_loaded()
        with self._lock:
            return self._statuses.get(agent_id)

    def add_agent(self, spec: AgentSpec) -> AgentSpec:
        self._ensure_loaded()
        with self._lock:
            self._agents[spec.agent_id] = spec
            self._statuses[spec.agent_id] = AgentStatus()
            self._persist()
            return spec

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        can_delegate: bool | None = None,
        can_be_delegated_to: bool | None = None,
        max_delegation_depth: int | None = None,
        enabled: bool | None = None,
    ) -> AgentSpec | None:
        self._ensure_loaded()
        with self._lock:
            spec = self._agents.get(agent_id)
            if spec is None:
                return None
            if name is not None:
                spec.name = name
            if description is not None:
                spec.description = description
            if system_prompt is not None:
                spec.system_prompt = system_prompt
            if model is not None:
                spec.model = model
            if allowed_tools is not None:
                spec.allowed_tools = list(allowed_tools)
            if can_delegate is not None:
                spec.can_delegate = can_delegate
            if can_be_delegated_to is not None:
                spec.can_be_delegated_to = can_be_delegated_to
            if max_delegation_depth is not None:
                spec.max_delegation_depth = max_delegation_depth
            if enabled is not None:
                spec.enabled = enabled
            self._persist()
            return spec

    def remove_agent(self, agent_id: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            if agent_id not in self._agents:
                return False
            del self._agents[agent_id]
            self._statuses.pop(agent_id, None)
            self._persist()
            return True

    def mark_invoked(self, agent_id: str) -> None:
        with self._lock:
            status = self._statuses.setdefault(agent_id, AgentStatus())
            status.last_invoked = time.time()
            status.invocation_count += 1

    def mark_error(self, agent_id: str, error: str) -> None:
        with self._lock:
            status = self._statuses.setdefault(agent_id, AgentStatus())
            status.healthy = False
            status.last_error = error

    def mark_healthy(self, agent_id: str) -> None:
        with self._lock:
            status = self._statuses.setdefault(agent_id, AgentStatus())
            status.healthy = True
            status.last_error = ""

    def _persist(self) -> None:
        if not self._overrides_path:
            return
        try:
            self._overrides_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "agents": [
                    self._agents[aid].to_dict()
                    for aid in sorted(self._agents.keys())
                ]
            }
            self._overrides_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error("agent persist failed: %s", e)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            total = len(self._agents)
            enabled = sum(1 for a in self._agents.values() if a.enabled)
            by_role: dict[str, int] = {}
            for a in self._agents.values():
                by_role[a.role] = by_role.get(a.role, 0) + 1
            return {
                "total_agents": total,
                "enabled_agents": enabled,
                "by_role": by_role,
            }


# --------------------------------------------------------------------- #
# 全局单例
# --------------------------------------------------------------------- #

_global_registry: AgentRegistry | None = None
_global_lock = threading.Lock()


def init_agent_registry(
    *,
    yaml_path: Path | None = None,
    overrides_path: Path | None = None,
) -> AgentRegistry | None:
    global _global_registry
    with _global_lock:
        if not yaml_path:
            return _global_registry
        _global_registry = AgentRegistry(
            yaml_path=yaml_path,
            overrides_path=overrides_path,
        )
        _global_registry.load()
        return _global_registry


def get_agent_registry() -> AgentRegistry | None:
    return _global_registry


def reset_agent_registry_for_tests() -> None:
    global _global_registry
    with _global_lock:
        _global_registry = None
