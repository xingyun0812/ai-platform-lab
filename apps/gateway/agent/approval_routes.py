from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.hitl import confirm_execution, list_pending, reject_execution
from packages.auth.rbac import can_approve_tools

router = APIRouter(prefix="/internal/agent", tags=["agent-internal"])


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as e:
        from fastapi import HTTPException

        if isinstance(e, HTTPException):
            return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))
        return json_error(401, "UNAUTHORIZED", str(e))


@router.get("/approvals/pending")
async def pending_approvals(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
    limit: int = 50,
) -> JSONResponse:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批执行")

    tenant_filter = None if caller.role == "platform_admin" else caller.tenant_id
    items = list_pending(tenant_id=tenant_filter, limit=limit)
    return JSONResponse(
        {
            "count": len(items),
            "items": [
                {
                    "approval_id": a.approval_id,
                    "tenant_id": a.tenant_id,
                    "session_id": a.session_id,
                    "tool_name": a.tool_name,
                    "arguments": a.arguments,
                    "status": a.status.value,
                    "created_at": a.created_at,
                }
                for a in items
            ],
        }
    )


@router.post("/approvals/{approval_id}/confirm")
async def confirm_approval(
    approval_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批执行")
    try:
        approval = confirm_execution(approval_id=approval_id, reviewer=caller.tenant_id)
    except ValueError as e:
        return json_error(404, "APPROVAL_NOT_FOUND", str(e))
    return JSONResponse(
        {
            "approval_id": approval.approval_id,
            "status": approval.status.value,
            "tool_name": approval.tool_name,
            "tenant_id": approval.tenant_id,
            "session_id": approval.session_id,
        }
    )


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批执行")
    try:
        approval = reject_execution(approval_id=approval_id, reviewer=caller.tenant_id)
    except ValueError as e:
        return json_error(404, "APPROVAL_NOT_FOUND", str(e))
    return JSONResponse({"approval_id": approval.approval_id, "status": approval.status.value})
