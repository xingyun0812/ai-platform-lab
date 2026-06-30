"""apps/gateway/agent/execution_handle_routes.py — 三层 ExecutionHandle 只读状态 API（#169 PR-2）。

Routes:
  GET /v1/agent/execution-status?plan_approval_id=...
  GET /v1/agent/execution-status?execution_id=...
  GET /v1/agent/execution-status?task_id=...
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import load_tenants
from packages.agent.execution_handle import (
    get_execution_handle_status,
    parse_execution_handle_lookup,
)

router = APIRouter(prefix="/v1/agent", tags=["execution-handle"])


def _checkpoint_store():
    from apps.gateway.settings import get_settings
    from packages.agent.graph_checkpoint import resolve_graph_checkpoint_store

    settings = get_settings()
    return resolve_graph_checkpoint_store(settings.redis_url)


@router.get("/execution-status", summary="统一只读 ExecutionHandle 状态查询")
async def get_unified_execution_status(
    plan_approval_id: Annotated[str | None, Query(description="Plan 审批层 handle")] = None,
    execution_id: Annotated[str | None, Query(description="Orchestrator execution_id")] = None,
    task_id: Annotated[str | None, Query(description="R2 long_run task_id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenants = load_tenants()
    try:
        tenant = resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            return json_error(int(exc.status_code), "UNAUTHORIZED", str(exc.detail))
        return json_error(401, "UNAUTHORIZED", str(exc))

    parsed = parse_execution_handle_lookup(
        plan_approval_id=plan_approval_id,
        execution_id=execution_id,
        task_id=task_id,
    )
    if isinstance(parsed, str):
        return json_error(400, "INVALID_HANDLE", parsed)

    layer, handle_id = parsed
    result = await get_execution_handle_status(
        layer,
        handle_id,
        tenant_id=tenant.tenant_id,
        checkpoint_store=_checkpoint_store(),
    )
    if result is None:
        return json_error(404, "HANDLE_NOT_FOUND", f"{layer} handle 不存在或无权访问: {handle_id}")

    return JSONResponse(content=result.to_dict())
