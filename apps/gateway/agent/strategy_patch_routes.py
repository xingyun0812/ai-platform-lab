"""apps/gateway/agent/strategy_patch_routes.py — Phase R R1 HITL 策略 patch REST（#146 7b）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.self_evolve import (
    StrategyPatch,
    approve_strategy_patch,
    get_strategy_patch_store,
    reject_strategy_patch,
)
from packages.auth.rbac import can_approve_tools, can_view_tenant_profile

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


def _patch_json(patch: StrategyPatch) -> dict:
    return patch.to_dict()


def _filter_patches_for_caller(
    patches: list[StrategyPatch],
    caller: TenantRecord,
) -> list[StrategyPatch]:
    if caller.role == "platform_admin":
        return patches
    return [p for p in patches if p.tenant_id == caller.tenant_id]


@router.get("/strategy-patches")
async def list_strategy_patches(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
    status: str | None = None,
    tenant_id: str | None = None,
) -> JSONResponse:
    """列出策略 patch（支持 ?status=pending|approved|rejected）。"""
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller

    if tenant_id and not can_view_tenant_profile(
        caller.role, caller.tenant_id, tenant_id
    ):
        return json_error(403, "FORBIDDEN", "无权查看该租户的策略 patch")

    store = get_strategy_patch_store()
    effective_tenant = tenant_id
    if caller.role != "platform_admin" and not tenant_id:
        effective_tenant = caller.tenant_id

    if status is not None or effective_tenant is not None:
        patches = store.list_by_status(status, tenant_id=effective_tenant)
    else:
        patches = store.list_all()

    items = _filter_patches_for_caller(patches, caller)
    return JSONResponse({"count": len(items), "items": [_patch_json(p) for p in items]})


def _get_patch_for_caller(
    patch_id: str,
    caller: TenantRecord,
) -> StrategyPatch | JSONResponse:
    patch = get_strategy_patch_store().get(patch_id)
    if patch is None:
        return json_error(404, "STRATEGY_PATCH_NOT_FOUND", f"patch 不存在: {patch_id}")
    if not can_view_tenant_profile(caller.role, caller.tenant_id, patch.tenant_id):
        return json_error(403, "FORBIDDEN", "无权操作该租户的策略 patch")
    return patch


@router.post("/strategy-patches/{patch_id}/approve")
async def approve_strategy_patch_route(
    patch_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批策略 patch")

    patch_or_err = _get_patch_for_caller(patch_id, caller)
    if isinstance(patch_or_err, JSONResponse):
        return patch_or_err

    ok = approve_strategy_patch(patch_id, decided_by=caller.tenant_id)
    if not ok:
        return json_error(404, "STRATEGY_PATCH_NOT_FOUND", f"patch 不存在: {patch_id}")

    updated = get_strategy_patch_store().get(patch_id)
    assert updated is not None
    return JSONResponse(_patch_json(updated))


@router.post("/strategy-patches/{patch_id}/reject")
async def reject_strategy_patch_route(
    patch_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller
    if not can_approve_tools(caller.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 审批策略 patch")

    patch_or_err = _get_patch_for_caller(patch_id, caller)
    if isinstance(patch_or_err, JSONResponse):
        return patch_or_err

    ok = reject_strategy_patch(patch_id, decided_by=caller.tenant_id)
    if not ok:
        return json_error(404, "STRATEGY_PATCH_NOT_FOUND", f"patch 不存在: {patch_id}")

    updated = get_strategy_patch_store().get(patch_id)
    assert updated is not None
    return JSONResponse(_patch_json(updated))
