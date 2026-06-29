"""packages/agent/plan_approval.py — Phase Q Q4 Plan-level HITL store.

提供内存审批存储（InMemory），与 Phase E 工具级 HITL 风格一致。
全局单例：get_plan_approval_store / reset_plan_approval_store_for_tests
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.contracts.agent_schemas import AgentPlan

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class PlanApprovalRequest:
    """Plan 级审批请求。"""

    plan_approval_id: str
    tenant_id: str
    session_id: str
    plan: AgentPlan
    status: str = "pending"  # pending | approved | rejected
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_approval_id": self.plan_approval_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "plan": {
                "goal": self.plan.goal,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "tool_hint": s.tool_hint,
                        "depends_on": s.depends_on,
                    }
                    for s in self.plan.steps
                ],
            },
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
        }


# ---------------------------------------------------------------------------
# 内存审批存储
# ---------------------------------------------------------------------------


class PlanApprovalStore:
    """线程安全的内存 Plan 审批存储。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: dict[str, PlanApprovalRequest] = {}

    def store(
        self,
        plan_approval_id: str,
        plan: AgentPlan,
        tenant_id: str,
        session_id: str = "",
    ) -> PlanApprovalRequest:
        """创建并存储一条 Plan 审批请求。"""
        req = PlanApprovalRequest(
            plan_approval_id=plan_approval_id,
            tenant_id=tenant_id,
            session_id=session_id,
            plan=plan,
        )
        with self._lock:
            self._store[plan_approval_id] = req
        return req

    def get(self, plan_approval_id: str) -> PlanApprovalRequest | None:
        """按 id 查询，不存在返回 None。"""
        with self._lock:
            return self._store.get(plan_approval_id)

    def approve(
        self,
        plan_approval_id: str,
        decided_by: str = "user",
        reason: str | None = None,
    ) -> bool:
        """审批通过，返回 True；id 不存在返回 False。"""
        with self._lock:
            req = self._store.get(plan_approval_id)
            if req is None:
                return False
            req.status = "approved"
            req.decided_at = time.time()
            req.decided_by = decided_by
            req.decision_reason = reason
            return True

    def reject(
        self,
        plan_approval_id: str,
        decided_by: str = "user",
        reason: str | None = None,
    ) -> bool:
        """拒绝，返回 True；id 不存在返回 False。"""
        with self._lock:
            req = self._store.get(plan_approval_id)
            if req is None:
                return False
            req.status = "rejected"
            req.decided_at = time.time()
            req.decided_by = decided_by
            req.decision_reason = reason
            return True

    def is_approved(self, plan_approval_id: str) -> bool:
        """返回该 id 是否已被 approved。"""
        with self._lock:
            req = self._store.get(plan_approval_id)
            return req is not None and req.status == "approved"


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_store: PlanApprovalStore | None = None


def get_plan_approval_store() -> PlanApprovalStore:
    global _store
    if _store is None:
        _store = PlanApprovalStore()
    return _store


def reset_plan_approval_store_for_tests() -> None:
    global _store
    _store = None


# ---------------------------------------------------------------------------
# 顶层便捷函数（供 planner.py 调用）
# ---------------------------------------------------------------------------


def store_plan_approval(
    plan_approval_id: str,
    plan: AgentPlan,
    tenant_id: str,
    session_id: str = "",
) -> PlanApprovalRequest:
    """存储 Plan 审批请求（便捷包装）。"""
    return get_plan_approval_store().store(
        plan_approval_id=plan_approval_id,
        plan=plan,
        tenant_id=tenant_id,
        session_id=session_id,
    )


def get_plan_approval(plan_approval_id: str) -> PlanApprovalRequest | None:
    """按 id 查询（便捷包装）。"""
    return get_plan_approval_store().get(plan_approval_id)


def approve_plan(
    plan_approval_id: str,
    decided_by: str = "user",
    reason: str | None = None,
) -> bool:
    """审批通过（便捷包装）。"""
    return get_plan_approval_store().approve(plan_approval_id, decided_by, reason)


def reject_plan(
    plan_approval_id: str,
    decided_by: str = "user",
    reason: str | None = None,
) -> bool:
    """拒绝（便捷包装）。"""
    return get_plan_approval_store().reject(plan_approval_id, decided_by, reason)


def is_plan_approved(plan_approval_id: str) -> bool:
    """是否已 approved（便捷包装）。"""
    return get_plan_approval_store().is_approved(plan_approval_id)


def new_plan_approval_id() -> str:
    """生成新的 plan_approval_id（UUID4）。"""
    return str(uuid.uuid4())
