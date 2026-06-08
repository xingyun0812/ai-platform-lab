from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.marketplace import (
    approve_tool_request,
    catalog_payload,
    create_tool_request,
    list_tool_requests,
    reject_tool_request,
)
from packages.providers.registry import matrix_payload
from packages.rag.vector_store import VectorStore
from packages.region.resolver import regions_payload
from packages.tenant_admin.overrides import patch_tenant_limits

router = APIRouter(prefix="/internal", tags=["platform"])


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


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if tenant.tenant_id != "admin":
        return json_error(403, "FORBIDDEN", "仅 admin 租户可执行该操作")
    return None


class TenantLimitsPatch(BaseModel):
    daily_request_quota: int | None = None
    token_budget_daily: int | None = None
    token_budget_monthly: int | None = None
    rate_limit_rps: float | None = Field(default=None, ge=0)
    rate_limit_burst: int | None = Field(default=None, ge=0)


class ToolRequestBody(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)


@router.get("/providers/matrix")
async def providers_matrix(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant
    return matrix_payload()


@router.get("/regions")
async def list_regions(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant
    return regions_payload()


@router.get("/tenants/{tenant_id}/profile")
async def tenant_profile(
    tenant_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if caller.tenant_id != "admin" and caller.tenant_id != tenant_id:
        return json_error(403, "FORBIDDEN", "仅可查看本租户或 admin 查看全部")

    record = tenants.get(tenant_id)
    if record is None:
        return json_error(404, "NOT_FOUND", f"租户不存在: {tenant_id}")

    kb_versions: dict[str, list[int]] = {}
    try:
        store = VectorStore()
        for kb in ("lab-demo",):
            try:
                kb_versions[kb] = store.list_versions(kb)
            except Exception:
                kb_versions[kb] = []
    except Exception:
        kb_versions = {}

    return {
        "tenant_id": tenant_id,
        "daily_request_quota": record.daily_request_quota,
        "token_budget_daily": record.token_budget_daily,
        "token_budget_monthly": record.token_budget_monthly,
        "rate_limit_rps": record.rate_limit_rps,
        "rate_limit_burst": record.rate_limit_burst,
        "allowed_models": list(record.allowed_models),
        "allowed_tools": list(record.allowed_tools),
        "default_model": record.default_model,
        "home_region": record.home_region,
        "data_zone": record.data_zone,
        "kb_versions": kb_versions,
    }


@router.patch("/tenants/{tenant_id}/limits")
async def patch_limits(
    tenant_id: str,
    body: TenantLimitsPatch,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    denied = _require_admin(caller)
    if denied is not None:
        return denied
    if tenant_id not in tenants:
        return json_error(404, "NOT_FOUND", f"租户不存在: {tenant_id}")

    patch = body.model_dump(exclude_none=True)
    if not patch:
        return json_error(400, "BAD_REQUEST", "无有效字段")
    updated = patch_tenant_limits(tenant_id, patch)
    return {"tenant_id": tenant_id, "overrides": updated, "reload_hint": "重启 gateway 或清 tenants 缓存后全量生效"}


@router.get("/tools/marketplace")
async def tools_marketplace(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant
    return catalog_payload()


@router.post("/tools/requests")
async def submit_tool_request(
    body: ToolRequestBody,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if caller.tenant_id != body.tenant_id and caller.tenant_id != "admin":
        return json_error(403, "FORBIDDEN", "仅可为自身租户提交申请")
    try:
        req = create_tool_request(tenant_id=body.tenant_id, tool_name=body.tool_name)
    except ValueError as e:
        return json_error(400, "BAD_REQUEST", str(e))
    return {
        "request_id": req.request_id,
        "tenant_id": req.tenant_id,
        "tool_name": req.tool_name,
        "status": req.status.value,
        "created_at": req.created_at,
        "updated_at": req.updated_at,
    }


@router.get("/tools/requests")
async def list_requests(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    tid = None if caller.tenant_id == "admin" else caller.tenant_id
    rows = list_tool_requests(tenant_id=tid)
    return {"requests": [r.__dict__ for r in rows]}


@router.post("/tools/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    denied = _require_admin(caller)
    if denied is not None:
        return denied
    req = approve_tool_request(request_id, reviewer=caller.tenant_id)
    if req is None:
        return json_error(404, "NOT_FOUND", f"申请不存在: {request_id}")
    return {"request_id": req.request_id, "status": req.status.value, "tool_name": req.tool_name}


@router.post("/tools/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    denied = _require_admin(caller)
    if denied is not None:
        return denied
    req = reject_tool_request(request_id, reviewer=caller.tenant_id)
    if req is None:
        return json_error(404, "NOT_FOUND", f"申请不存在: {request_id}")
    return {"request_id": req.request_id, "status": req.status.value}
