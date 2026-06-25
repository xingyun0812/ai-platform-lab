"""统一图状态 — 最小 LangGraph 等价物的状态载体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from packages.contracts.agent_schemas import AgentPlan

GraphMode = Literal["react", "plan", "workflow"]
GraphStatus = Literal["running", "paused", "completed", "failed"]


@dataclass
class AgentGraphState:
    """跨 Plan / ReAct / Orchestrator 的统一状态快照。"""

    tenant_id: str
    session_id: str
    mode: GraphMode
    status: GraphStatus = "running"
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: AgentPlan | None = None
    workflow_id: str | None = None
    current_node: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    interrupt_reason: str | None = None
    interrupt_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "status": self.status,
            "messages": self.messages,
            "plan": self.plan.model_dump() if self.plan is not None else None,
            "workflow_id": self.workflow_id,
            "current_node": self.current_node,
            "variables": self.variables,
            "interrupt_reason": self.interrupt_reason,
            "interrupt_id": self.interrupt_id,
        }
