"""Multi-Agent 管理 REST API — Phase H #38

路由前缀：/internal/agents

接口：
    GET    /internal/agents                    列出所有 Agent
    GET    /internal/agents/{agent_id}          获取详情
    POST   /internal/agents                    注册新 Agent（admin）
    PATCH  /internal/agents/{agent_id}          更新（admin）
    DELETE /internal/agents/{agent_id}          删除（admin）
    POST   /internal/agents/{agent_id}/delegate  委托任务给 Agent
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.agent.multi_agent import (
    AgentSpec,
    delegate_to_agent,
    get_agent_registry,
)
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/agents", tags=["multi_agent"])


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


def _registry_or_503():
    reg = get_agent_registry()
    if reg is None:
        return json_error(503, "MULTI_AGENT_DISABLED", "MULTI_AGENT_ENABLED=false")
    return reg


class AgentCreateRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    name: str
    role: str = "specialist"  # primary | specialist | reviewer | router
    description: str = ""
    system_prompt: str = ""
    model: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    can_delegate: bool = False
    can_be_delegated_to: bool = True
    max_delegation_depth: int = 3
    enabled: bool = True


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    can_delegate: bool | None = None
    can_be_delegated_to: bool | None = None
    max_delegation_depth: int | None = None
    enabled: bool | None = None


class DelegateRequest(BaseModel):
    task: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 60.0


def _agent_payload(spec: AgentSpec, status: Any = None) -> dict[str, Any]:
    out = spec.to_dict()
    if status is not None:
        out["status"] = {
            "healthy": status.healthy,
            "last_invoked": status.last_invoked,
            "invocation_count": status.invocation_count,
            "last_error": status.last_error,
        }
    return out


@router.get("")
async def list_agents(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    agents = reg.list_agents()
    return JSONResponse(
        {
            "agents": [
                _agent_payload(a, reg.get_status(a.agent_id)) for a in agents
            ],
            "stats": reg.stats(),
        }
    )


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    spec = reg.get_agent(agent_id)
    if spec is None:
        return json_error(404, "NOT_FOUND", f"Agent {agent_id} 不存在")
    return JSONResponse(_agent_payload(spec, reg.get_status(agent_id)))


@router.post("")
async def create_agent(
    body: AgentCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    if body.role not in ("primary", "specialist", "reviewer", "router"):
        return json_error(400, "INVALID_ROLE", "role 必须是 primary/specialist/reviewer/router")
    existing = reg.get_agent(body.agent_id)
    if existing is not None:
        return json_error(409, "ALREADY_EXISTS", f"Agent {body.agent_id} 已存在")
    spec = AgentSpec(
        agent_id=body.agent_id,
        name=body.name,
        role=body.role,
        description=body.description,
        system_prompt=body.system_prompt,
        model=body.model,
        allowed_tools=body.allowed_tools,
        can_delegate=body.can_delegate,
        can_be_delegated_to=body.can_be_delegated_to,
        max_delegation_depth=body.max_delegation_depth,
        enabled=body.enabled,
        created_by=tenant.tenant_id,
    )
    reg.add_agent(spec)
    return JSONResponse(
        _agent_payload(spec, reg.get_status(spec.agent_id)),
        status_code=201,
    )


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    spec = reg.update_agent(
        agent_id,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        model=body.model,
        allowed_tools=body.allowed_tools,
        can_delegate=body.can_delegate,
        can_be_delegated_to=body.can_be_delegated_to,
        max_delegation_depth=body.max_delegation_depth,
        enabled=body.enabled,
    )
    if spec is None:
        return json_error(404, "NOT_FOUND", f"Agent {agent_id} 不存在")
    return JSONResponse(_agent_payload(spec, reg.get_status(agent_id)))


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    ok = reg.remove_agent(agent_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"Agent {agent_id} 不存在")
    return JSONResponse({"agent_id": agent_id, "deleted": True})


@router.post("/{agent_id}/delegate")
async def delegate_to_agent_api(
    agent_id: str,
    body: DelegateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """委托任务给指定 Agent。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    spec = reg.get_agent(agent_id)
    if spec is None:
        return json_error(404, "NOT_FOUND", f"Agent {agent_id} 不存在")
    result = await delegate_to_agent(
        agent_id=agent_id,
        task=body.task,
        inputs=body.inputs,
        timeout_seconds=body.timeout_seconds,
    )
    return JSONResponse(
        {
            "agent_id": result.agent_id,
            "task": result.task,
            "status": result.status,
            "output": result.output,
            "error": result.error,
            "usage": result.usage,
            "execution_time_ms": round(result.execution_time_ms, 2),
            "delegation_depth": result.delegation_depth,
        }
    )
