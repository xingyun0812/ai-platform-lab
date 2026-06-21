"""Prompt 版本管理 REST API — Phase F #29

路由前缀：/internal/prompts

接口：
    GET    /internal/prompts                           列出所有 prompt_id
    GET    /internal/prompts/{prompt_id}               获取 active 版本
    GET    /internal/prompts/{prompt_id}/versions      列出所有版本
    POST   /internal/prompts/{prompt_id}/versions      创建新版本（admin）
    PATCH  /internal/prompts/{prompt_id}/active        切换 active 版本（admin）
    POST   /internal/prompts/{prompt_id}/render        渲染模板（传入变量）

权限：
    GET：任何已认证租户
    POST/PATCH：仅 platform_admin 角色
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.prompt import (
    PromptRegistry,
    PromptRegistryError,
    PromptVersion,
    get_registry,
)

router = APIRouter(prefix="/internal/prompts", tags=["prompt"])


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


def _registry() -> PromptRegistry | JSONResponse:
    reg = get_registry()
    if reg is None:
        return json_error(503, "PROMPT_REGISTRY_DISABLED", "PROMPT_REGISTRY_ENABLED=false")
    return reg


class CreateVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)
    changelog: str = ""
    set_active: bool = True


class SetActiveRequest(BaseModel):
    version: int = Field(..., ge=1)


class RenderRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


def _version_payload(v: PromptVersion) -> dict[str, Any]:
    return {
        "prompt_id": v.prompt_id,
        "version": v.version,
        "status": v.status,
        "tenant_id": v.tenant_id,
        "changelog": v.changelog,
        "created_at": v.created_at,
        "created_by": v.created_by,
        "variables": v.variables,
        "content": v.content,
    }


@router.get("")
async def list_prompts(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    ids = reg.list_prompt_ids()
    stats = reg.stats()
    return JSONResponse(
        {
            "prompt_ids": ids,
            "stats": stats,
            "tenant_id": tenant.tenant_id,
        }
    )


@router.get("/{prompt_id}")
async def get_active(
    prompt_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    v = reg.get_active(prompt_id)
    if v is None:
        return json_error(404, "PROMPT_NOT_FOUND", f"prompt_id={prompt_id} 未找到 active 版本")
    return JSONResponse(_version_payload(v))


@router.get("/{prompt_id}/versions")
async def list_versions(
    prompt_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    versions = reg.list_versions(prompt_id)
    return JSONResponse(
        {
            "prompt_id": prompt_id,
            "versions": [_version_payload(v) for v in versions],
            "count": len(versions),
        }
    )


@router.post("/{prompt_id}/versions")
async def create_version(
    prompt_id: str,
    body: CreateVersionRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    try:
        v = reg.create_version(
            prompt_id=prompt_id,
            content=body.content,
            changelog=body.changelog,
            created_by=tenant.tenant_id,
            set_active=body.set_active,
        )
    except PromptRegistryError as e:
        return json_error(400, e.code, e.message)
    return JSONResponse(_version_payload(v), status_code=201)


@router.patch("/{prompt_id}/active")
async def set_active(
    prompt_id: str,
    body: SetActiveRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    try:
        v = reg.set_active(prompt_id, body.version)
    except PromptRegistryError as e:
        return json_error(404, e.code, e.message)
    return JSONResponse(_version_payload(v))


@router.post("/{prompt_id}/render")
async def render_prompt(
    prompt_id: str,
    body: RenderRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    reg = _registry()
    if isinstance(reg, JSONResponse):
        return reg
    try:
        rendered, v = reg.render(prompt_id, body.variables)
    except PromptRegistryError as e:
        return json_error(404, e.code, e.message)
    return JSONResponse(
        {
            "prompt_id": prompt_id,
            "version": v.version if v else None,
            "rendered": rendered,
            "rendered_at": time.time(),
        }
    )
