"""HITL 审批工作流 REST API — Phase H #40

路由前缀：/internal/hitl

接口：
    POST   /internal/hitl/approvals                         创建审批请求
    GET    /internal/hitl/approvals/{request_id}            查询审批状态
    GET    /internal/hitl/approvals                         列出待审批（?tenant_id=&status=）
    POST   /internal/hitl/approvals/{request_id}/approve    批准（admin）
    POST   /internal/hitl/approvals/{request_id}/reject     拒绝（admin）
    POST   /internal/hitl/approvals/{request_id}/cancel     取消（admin）
    POST   /internal/hitl/webhooks/test                     测试 webhook（admin）
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

logger = logging.getLogger("ai_platform.hitl.routes")

router = APIRouter(prefix="/internal/hitl", tags=["hitl"])


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if not can_patch_tenant_limits(tenant.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _store_or_503() -> JSONResponse | object:
    from packages.hitl import get_approval_store  # noqa: PLC0415
    store = get_approval_store()
    if store is None:
        return json_error(503, "HITL_DISABLED", "HITL 未初始化，请设置 HITL_ENABLED=true")
    return store


def _req_to_dict(req) -> dict:
    return {
        "request_id": req.request_id,
        "tenant_id": req.tenant_id,
        "session_id": req.session_id,
        "tool_name": req.tool_name,
        "arguments": req.arguments,
        "created_at": req.created_at,
        "expires_at": req.expires_at,
        "status": req.status,
        "decided_by": req.decided_by,
        "decided_at": req.decided_at,
        "decision_reason": req.decision_reason,
        "webhook_sent": req.webhook_sent,
        "metadata": req.metadata,
    }


# ---------------------------------------------------------------------------
# Pydantic 请求体
# ---------------------------------------------------------------------------

class CreateApprovalRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    arguments: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    metadata: dict = Field(default_factory=dict)
    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_headers: dict = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    decided_by: str = Field(..., min_length=1)
    reason: str | None = None


class WebhookTestRequest(BaseModel):
    url: str = Field(..., min_length=1)
    secret: str = ""
    headers: dict = Field(default_factory=dict)
    payload: dict = Field(default_factory=lambda: {"event": "hitl.test"})


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/approvals")
async def create_approval(
    body: CreateApprovalRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """创建 HITL 审批请求。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    from packages.hitl.service import request_approval  # noqa: PLC0415
    from packages.hitl.store import WebhookConfig  # noqa: PLC0415

    webhook = None
    if body.webhook_url:
        webhook = WebhookConfig(
            url=body.webhook_url,
            headers=body.webhook_headers,
            secret=body.webhook_secret or "",
            enabled=True,
        )

    try:
        req = await request_approval(
            tenant_id=body.tenant_id,
            session_id=body.session_id,
            tool_name=body.tool_name,
            arguments=body.arguments,
            timeout_seconds=body.timeout_seconds,
            webhook=webhook,
            metadata=body.metadata,
        )
        return JSONResponse(_req_to_dict(req), status_code=201)
    except Exception as e:
        logger.exception("创建审批请求失败")
        return json_error(500, "INTERNAL_ERROR", str(e))


@router.get("/approvals/{request_id}")
async def get_approval(
    request_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查询单条审批请求状态。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    req = await store.get(request_id)
    if req is None:
        return json_error(404, "NOT_FOUND", f"审批请求不存在: {request_id}")
    return JSONResponse(_req_to_dict(req))


@router.get("/approvals")
async def list_approvals(
    tenant_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = "pending",
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """列出待审批请求（或按状态过滤）。"""
    caller = _resolve(x_tenant_id, authorization)
    if isinstance(caller, JSONResponse):
        return caller

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    target_tenant = tenant_id or caller.tenant_id
    reqs = await store.list_pending(target_tenant)

    # 若指定了非 pending 状态，暂无跨状态批量查询接口，返回空（扩展点）
    if status and status != "pending":
        reqs = []

    return JSONResponse(
        {
            "approvals": [_req_to_dict(r) for r in reqs],
            "count": len(reqs),
            "tenant_id": target_tenant,
            "status_filter": status,
        }
    )


@router.post("/approvals/{request_id}/approve")
async def approve_request(
    request_id: str,
    body: DecisionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """批准审批请求（需 platform_admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    from packages.hitl.service import approve  # noqa: PLC0415

    try:
        req = await approve(request_id, decided_by=body.decided_by, reason=body.reason)
        return JSONResponse(_req_to_dict(req))
    except ValueError as e:
        return json_error(400, "APPROVAL_ERROR", str(e))
    except Exception as e:
        return json_error(500, "INTERNAL_ERROR", str(e))


@router.post("/approvals/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: DecisionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """拒绝审批请求（需 platform_admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    from packages.hitl.service import reject  # noqa: PLC0415

    try:
        req = await reject(request_id, decided_by=body.decided_by, reason=body.reason)
        return JSONResponse(_req_to_dict(req))
    except ValueError as e:
        return json_error(400, "APPROVAL_ERROR", str(e))
    except Exception as e:
        return json_error(500, "INTERNAL_ERROR", str(e))


@router.post("/approvals/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    body: DecisionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """取消审批请求（需 platform_admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err

    store = _store_or_503()
    if isinstance(store, JSONResponse):
        return store

    ok = await store.cancel(request_id, by=body.decided_by)
    if not ok:
        return json_error(400, "APPROVAL_ERROR", f"取消失败（不存在或已处理）: {request_id}")
    return JSONResponse({"request_id": request_id, "cancelled": True})


@router.post("/webhooks/test")
async def test_webhook(
    body: WebhookTestRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """测试 webhook 配置（需 platform_admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err

    from packages.hitl.store import WebhookConfig  # noqa: PLC0415
    from packages.hitl.webhook import send_webhook  # noqa: PLC0415

    config = WebhookConfig(
        url=body.url,
        headers=body.headers,
        secret=body.secret,
        enabled=True,
    )
    success = await send_webhook(config, body.payload)
    return JSONResponse({"url": body.url, "success": success})
