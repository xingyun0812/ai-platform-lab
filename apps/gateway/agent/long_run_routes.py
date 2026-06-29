"""apps/gateway/agent/long_run_routes.py — Phase R R2 长程任务 API。

Routes:
  POST /v1/agent/long-run                        — 创建长程任务
  GET  /v1/agent/long-run/{task_id}              — 查询状态 + step 进度
  GET  /v1/agent/long-run                        — 列出租户的所有任务
  POST /v1/agent/long-run/{task_id}/resume       — 续跑
  POST /v1/agent/long-run/{task_id}/cancel       — 取消
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.long_horizon import (
    cancel_task,
    create_long_run,
    get_long_run,
    get_long_run_store,
    get_task_status,
    resume_task,
)
from packages.contracts.agent_schemas import AgentPlan

logger = logging.getLogger("ai_platform.gateway.agent.long_run")

router = APIRouter(prefix="/v1/agent/long-run", tags=["agent-long-run"])


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateLongRunRequest(BaseModel):
    plan: AgentPlan = Field(..., description="AgentPlan 对象")
    session_id: str = Field(default="", description="关联的 session id")
    metadata: dict[str, Any] = Field(default_factory=dict, description="任意元数据")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", summary="创建长程任务")
async def create_long_run_task(
    body: CreateLongRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    task = await create_long_run(
        plan=body.plan,
        tenant_id=tenant.tenant_id,
        session_id=body.session_id,
    )
    if body.metadata:
        task.metadata.update(body.metadata)

    return JSONResponse(
        status_code=201,
        content={
            "task_id": task.task_id,
            "status": task.status,
            "tenant_id": task.tenant_id,
            "progress": task.progress(),
        },
    )


@router.get("", summary="列出租户的所有长程任务")
async def list_long_run_tasks(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    store = get_long_run_store()
    tasks = await store.list_by_tenant(tenant.tenant_id)
    return JSONResponse(
        content={
            "tasks": [
                {
                    **t.to_dict(),
                    "progress": t.progress(),
                }
                for t in tasks
            ],
            "total": len(tasks),
        }
    )


@router.get("/{task_id}", summary="查询长程任务状态 + step 进度")
async def get_long_run_task(
    task_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    task = await get_long_run(task_id)
    if task is None:
        return json_error(404, "TASK_NOT_FOUND", f"长程任务不存在: {task_id}")

    if task.tenant_id != tenant.tenant_id:
        return json_error(403, "FORBIDDEN", "无权访问此任务")

    status_dict = await get_task_status(task_id)
    return JSONResponse(content=status_dict)


@router.post("/{task_id}/resume", summary="续跑长程任务")
async def resume_long_run_task(
    task_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    task = await get_long_run(task_id)
    if task is None:
        return json_error(404, "TASK_NOT_FOUND", f"长程任务不存在: {task_id}")

    if task.tenant_id != tenant.tenant_id:
        return json_error(403, "FORBIDDEN", "无权访问此任务")

    if task.status in {"completed", "cancelled"}:
        return json_error(409, "TASK_NOT_RESUMABLE", f"任务状态 {task.status} 不可续跑")

    updated = await resume_task(task_id)
    if updated is None:
        return json_error(500, "RESUME_FAILED", "续跑失败")

    return JSONResponse(
        content={
            "task_id": task_id,
            "status": updated.status,
            "progress": updated.progress(),
            "checkpoint_count": len(updated.checkpoints),
        }
    )


@router.post("/{task_id}/cancel", summary="取消长程任务")
async def cancel_long_run_task(
    task_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    task = await get_long_run(task_id)
    if task is None:
        return json_error(404, "TASK_NOT_FOUND", f"长程任务不存在: {task_id}")

    if task.tenant_id != tenant.tenant_id:
        return json_error(403, "FORBIDDEN", "无权访问此任务")

    ok = await cancel_task(task_id)
    if not ok:
        return json_error(409, "CANCEL_FAILED", f"任务状态 {task.status} 无法取消")

    updated = await get_long_run(task_id)
    return JSONResponse(
        content={
            "task_id": task_id,
            "status": updated.status if updated else "cancelled",
        }
    )
