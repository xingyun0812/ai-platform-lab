"""MCP Server 管理 REST API — Phase F #32

路由前缀：/internal/mcp

接口：
    GET    /internal/mcp/servers                   列出所有 MCP server
    GET    /internal/mcp/servers/{server_id}       获取单个 server 详情
    POST   /internal/mcp/servers                   注册新 server（admin）
    PATCH  /internal/mcp/servers/{server_id}       更新 server（admin）
    DELETE /internal/mcp/servers/{server_id}       删除 server（admin）
    POST   /internal/mcp/servers/{server_id}/test  测试连接（admin）
    GET    /internal/mcp/servers/{server_id}/tools 列出 server 提供的工具
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.mcp import (
    MCPClient,
    MCPClientError,
    MCPServerConfig,
    get_mcp_registry,
)

router = APIRouter(prefix="/internal/mcp", tags=["mcp"])


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
    reg = get_mcp_registry()
    if reg is None:
        return json_error(503, "MCP_DISABLED", "MCP_ENABLED=false 或未初始化")
    return reg


class ServerCreateRequest(BaseModel):
    server_id: str = Field(..., min_length=1)
    transport: str = Field(..., description="stdio | http")
    enabled: bool = True
    # stdio
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    api_key: str = ""
    description: str = ""


class ServerUpdateRequest(BaseModel):
    enabled: bool | None = None
    command: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    api_key: str | None = None
    description: str | None = None


def _config_payload(cfg: MCPServerConfig, status: Any = None) -> dict[str, Any]:
    out = cfg.to_dict()
    if status is not None:
        out["status"] = {
            "healthy": status.healthy,
            "last_check": status.last_check,
            "last_error": status.last_error,
            "tools_count": status.tools_count,
        }
    return out


@router.get("/servers")
async def list_servers(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    servers = reg.list_servers()
    return JSONResponse(
        {
            "servers": [
                _config_payload(s, reg.get_status(s.server_id)) for s in servers
            ],
            "stats": reg.stats(),
        }
    )


@router.get("/servers/{server_id}")
async def get_server(
    server_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    cfg = reg.get_server(server_id)
    if cfg is None:
        return json_error(404, "NOT_FOUND", f"MCP server {server_id} 不存在")
    return JSONResponse(
        _config_payload(cfg, reg.get_status(server_id))
    )


@router.post("/servers")
async def create_server(
    body: ServerCreateRequest,
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
    if body.transport not in ("stdio", "http"):
        return json_error(400, "INVALID_TRANSPORT", "transport 必须是 stdio 或 http")
    if body.transport == "stdio" and not body.command:
        return json_error(400, "INVALID_CONFIG", "stdio transport 需要 command")
    if body.transport == "http" and not body.url:
        return json_error(400, "INVALID_CONFIG", "http transport 需要 url")
    existing = reg.get_server(body.server_id)
    if existing is not None:
        return json_error(409, "ALREADY_EXISTS", f"MCP server {body.server_id} 已存在")
    cfg = MCPServerConfig(
        server_id=body.server_id,
        transport=body.transport,
        enabled=body.enabled,
        command=body.command,
        env=body.env,
        url=body.url,
        headers=body.headers,
        api_key=body.api_key,
        description=body.description,
        created_by=tenant.tenant_id,
    )
    reg.add_server(cfg)
    return JSONResponse(
        _config_payload(cfg, reg.get_status(cfg.server_id)),
        status_code=201,
    )


@router.patch("/servers/{server_id}")
async def update_server(
    server_id: str,
    body: ServerUpdateRequest,
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
    cfg = reg.update_server(
        server_id,
        enabled=body.enabled,
        command=body.command,
        env=body.env,
        url=body.url,
        headers=body.headers,
        api_key=body.api_key,
        description=body.description,
    )
    if cfg is None:
        return json_error(404, "NOT_FOUND", f"MCP server {server_id} 不存在")
    return JSONResponse(_config_payload(cfg, reg.get_status(server_id)))


@router.delete("/servers/{server_id}")
async def delete_server(
    server_id: str,
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
    ok = reg.remove_server(server_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"MCP server {server_id} 不存在")
    return JSONResponse({"server_id": server_id, "deleted": True})


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """测试 MCP server 连接：initialize + tools/list。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    cfg = reg.get_server(server_id)
    if cfg is None:
        return json_error(404, "NOT_FOUND", f"MCP server {server_id} 不存在")
    try:
        client = MCPClient(cfg)
        await client.connect(timeout=5.0)
        tools = await client.list_tools(timeout=5.0)
        reg.mark_healthy(server_id, len(tools))
        return JSONResponse(
            {
                "server_id": server_id,
                "connected": True,
                "server_info": {
                    "name": client._server_info.name if client._server_info else None,
                    "version": client._server_info.version if client._server_info else None,
                },
                "tools_count": len(tools),
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description[:100],
                    }
                    for t in tools
                ],
            }
        )
    except MCPClientError as e:
        reg.mark_unhealthy(server_id, e.message)
        return json_error(502, "MCP_CONNECT_FAILED", e.message)
    except Exception as e:
        reg.mark_unhealthy(server_id, str(e))
        return json_error(500, "INTERNAL_ERROR", str(e))


@router.get("/servers/{server_id}/tools")
async def list_server_tools(
    server_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry_or_503()
    if isinstance(reg, JSONResponse):
        return reg
    cfg = reg.get_server(server_id)
    if cfg is None:
        return json_error(404, "NOT_FOUND", f"MCP server {server_id} 不存在")
    try:
        client = MCPClient(cfg)
        await client.connect(timeout=5.0)
        tools = await client.list_tools(timeout=5.0)
        reg.mark_healthy(server_id, len(tools))
        return JSONResponse(
            {
                "server_id": server_id,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.input_schema,
                    }
                    for t in tools
                ],
                "count": len(tools),
            }
        )
    except MCPClientError as e:
        reg.mark_unhealthy(server_id, e.message)
        return json_error(502, "MCP_LIST_FAILED", e.message)
