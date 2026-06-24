"""apps/gateway/agent/plan_approval_routes.py — Plan-level HITL API (Phase Q Q4).

Routes:
  GET  /v1/agent/plan/approval/{plan_approval_id}         — 查询 plan 审批状态
  POST /v1/agent/plan/approval/{plan_approval_id}/approve — 审批通过
  POST /v1/agent/plan/approval/{plan_approval_id}/reject  — 拒绝

需要 platform_admin 角色（与工具级审批保持一致）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_approve_tools

router = APIRouter(prefix="/v1/agent/plan", tags=["plan-approval"])


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            return json_error(int(exc.status_code), "UNAUTHORIZED", str(exc.detail))
        return json_error(401, "UNAUTHORIZED", str(exc))


def _plan_to_dict(plan: Any) -> dict[str, Any] | None:
    """将 AgentPlan（或 dict）序列化为 JSON-safe dict。"""
    if plan is None:
        return None
    if hasattr(plan, "model_dump"):
        return plan.model_dump()
    if isinstance(plan, dict):
        return plan
    # 尝试通用序列化（如 dataclass 或带 __dict__ 的对象）
    try:
        return {
            "goal": str(getattr(plan, "goal", "")),
            "steps_count": len(getattr(plan, "steps", [])),
        }
    except Exception:
        return {"plan": str(plan)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/approval/{plan_approval_id}")
async def get_plan_approval_status(
    plan_approval_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查询 plan 审批记录（plan JSON + status）。"""
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 查询 plan 审批")

    from packages.agent.plan_approval import get_plan_approval

    entry = get_plan_approval(plan_approval_id)
    if entry is None:
        return json_error(
            404, "PLAN_APPROVAL_NOT_FOUND", f"plan_approval_id 不存在: {plan_approval_id}"
        )

    return JSONResponse(
        {
            "plan_approval_id": plan_approval_id,
            "tenant_id": entry.tenant_id if hasattr(entry, "tenant_id") else entry.get("tenant_id"),
            "status": entry.status if hasattr(entry, "status") else entry.get("status"),
            "created_at": entry.created_at
            if hasattr(entry, "created_at")
            else entry.get("created_at"),
            "plan": _plan_to_dict(entry.plan if hasattr(entry, "plan") else entry.get("plan")),
        }
    )


@router.post("/approval/{plan_approval_id}/approve")
async def approve_plan_approval(
    plan_approval_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """审批通过指定 plan。"""
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批 plan")

    from packages.agent.plan_approval import approve_plan, get_plan_approval

    entry = get_plan_approval(plan_approval_id)
    if entry is None:
        return json_error(
            404, "PLAN_APPROVAL_NOT_FOUND", f"plan_approval_id 不存在: {plan_approval_id}"
        )

    ok = approve_plan(plan_approval_id)
    if not ok:
        return json_error(404, "PLAN_APPROVAL_NOT_FOUND", f"approve 失败: {plan_approval_id}")

    return JSONResponse(
        {
            "plan_approval_id": plan_approval_id,
            "status": "approved",
            "approved_by": caller.tenant_id,
        }
    )


@router.post("/approval/{plan_approval_id}/reject")
async def reject_plan_approval(
    plan_approval_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """拒绝指定 plan。"""
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 拒绝 plan")

    from packages.agent.plan_approval import get_plan_approval, reject_plan

    entry = get_plan_approval(plan_approval_id)
    if entry is None:
        return json_error(
            404, "PLAN_APPROVAL_NOT_FOUND", f"plan_approval_id 不存在: {plan_approval_id}"
        )

    reject_plan(plan_approval_id)

    return JSONResponse(
        {
            "plan_approval_id": plan_approval_id,
            "status": "rejected",
            "rejected_by": caller.tenant_id,
        }
    )
