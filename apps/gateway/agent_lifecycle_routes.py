"""Agent 生命周期管理 REST API — Phase H #39

路由前缀：/internal/agent-lifecycle

接口：
    POST   /internal/agent-lifecycle/{agent_id}/versions         注册新版本（admin）
    GET    /internal/agent-lifecycle/{agent_id}/versions         列出版本
    GET    /internal/agent-lifecycle/versions/{version_id}       获取版本详情
    POST   /internal/agent-lifecycle/versions/{version_id}/activate  激活版本（admin）
    POST   /internal/agent-lifecycle/{agent_id}/rollback         回滚到前一版本（admin）
    GET    /internal/agent-lifecycle/{agent_id}/active           获取当前激活版本
    PATCH  /internal/agent-lifecycle/{agent_id}/traffic          设置流量分配（admin）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/agent-lifecycle", tags=["agent_lifecycle"])


# --------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------- #

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


def _registry_or_503() -> Any:
    from packages.agent.lifecycle import get_lifecycle_registry
    reg = get_lifecycle_registry()
    if reg is None:
        return json_error(503, "LIFECYCLE_DISABLED", "AGENT_LIFECYCLE_ENABLED=false 或未初始化")
    return reg


# --------------------------------------------------------------------- #
# 请求体模型
# --------------------------------------------------------------------- #

class RegisterVersionRequest(BaseModel):
    spec_snapshot: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ActivateVersionRequest(BaseModel):
    strategy: str = Field(default="all_at_once", description="all_at_once | blue_green | canary")


class TrafficSplitRequest(BaseModel):
    splits: dict = Field(..., description="version_id → percent, 合计 100")


# --------------------------------------------------------------------- #
# 接口实现
# --------------------------------------------------------------------- #

@router.post("/{agent_id}/versions")
async def register_version(
    agent_id: str,
    body: RegisterVersionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """注册新 Agent 版本（admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    ver = reg.register_version(
        agent_id=agent_id,
        spec_snapshot=body.spec_snapshot,
        created_by=tenant.tenant_id,
        metadata=body.metadata,
    )
    return JSONResponse(ver.to_dict(), status_code=201)


@router.get("/{agent_id}/versions")
async def list_versions(
    agent_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """列出某 Agent 的所有版本。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    versions = reg.list_versions(agent_id)
    return JSONResponse({
        "agent_id": agent_id,
        "versions": [v.to_dict() for v in versions],
        "count": len(versions),
    })


@router.get("/versions/{version_id}")
async def get_version(
    version_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """获取版本详情。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    ver = reg.get_version(version_id)
    if ver is None:
        return json_error(404, "NOT_FOUND", f"version {version_id} 不存在")
    return JSONResponse(ver.to_dict())


@router.post("/versions/{version_id}/activate")
async def activate_version(
    version_id: str,
    body: ActivateVersionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """激活指定版本（admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    try:
        rs = reg.activate_version(version_id, strategy=body.strategy)
    except KeyError as e:
        return json_error(404, "NOT_FOUND", str(e))
    except ValueError as e:
        return json_error(400, "INVALID_STRATEGY", str(e))
    return JSONResponse(rs.to_dict())


@router.post("/{agent_id}/rollback")
async def rollback_version(
    agent_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """回滚到前一版本（admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    rs = reg.rollback_version(agent_id)
    if rs is None:
        return json_error(409, "NO_PREVIOUS_VERSION", f"agent {agent_id} 无可回滚的前一版本")
    return JSONResponse(rs.to_dict())


@router.get("/{agent_id}/active")
async def get_active_version(
    agent_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """获取当前激活版本。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    ver = reg.get_active(agent_id)
    if ver is None:
        return json_error(404, "NO_ACTIVE_VERSION", f"agent {agent_id} 无激活版本")
    return JSONResponse(ver.to_dict())


@router.patch("/{agent_id}/traffic")
async def set_traffic_split(
    agent_id: str,
    body: TrafficSplitRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """设置流量分配（用于 canary/blue_green，admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    try:
        rs = reg.set_traffic_split(agent_id, splits=body.splits)
    except KeyError as e:
        return json_error(404, "NOT_FOUND", str(e))
    except ValueError as e:
        return json_error(400, "INVALID_TRAFFIC_SPLIT", str(e))
    return JSONResponse(rs.to_dict())
