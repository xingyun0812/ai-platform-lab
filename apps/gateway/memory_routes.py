"""长记忆 REST API — Phase F #31

路由前缀：/internal/memory

接口：
    POST   /internal/memory                   创建记忆（admin）
    GET    /internal/memory/{memory_id}        获取单条
    POST   /internal/memory/search           搜索（按 scope + scope_id + query）
    GET    /internal/memory/list              列出（按 scope + scope_id）
    DELETE /internal/memory/{memory_id}       删除（admin）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.memory import MemoryRecord, MemoryStore, get_memory_store

router = APIRouter(prefix="/internal/memory", tags=["memory"])


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


def _store() -> MemoryStore | JSONResponse:
    s = get_memory_store()
    if s is None:
        return json_error(503, "MEMORY_STORE_DISABLED", "MEMORY_STORE_ENABLED=false")
    return s


def _record_payload(r: MemoryRecord) -> dict[str, Any]:
    return {
        "memory_id": r.memory_id,
        "tenant_id": r.tenant_id,
        "scope": r.scope,
        "scope_id": r.scope_id,
        "content": r.content,
        "summary": r.summary,
        "metadata": r.metadata,
        "created_at": r.created_at,
        "expires_at": r.expires_at,
        "has_embedding": r.embedding is not None,
    }


class CreateMemoryRequest(BaseModel):
    scope: str = Field(..., description="session | user | tenant")
    scope_id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=0, ge=0, description="0 表示不过期")


class SearchRequest(BaseModel):
    scope: str = Field(...)
    scope_id: str = Field(...)
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


@router.post("")
async def create_memory(
    body: CreateMemoryRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    import time as _time

    from packages.memory.store import _gen_id

    expires_at = (
        _time.time() + body.ttl_seconds if body.ttl_seconds > 0 else None
    )
    record = MemoryRecord(
        memory_id=_gen_id(),
        tenant_id=tenant.tenant_id,
        scope=body.scope,
        scope_id=body.scope_id,
        content=body.content,
        summary=body.summary,
        metadata={
            **body.metadata,
            "created_by": tenant.tenant_id,
        },
        expires_at=expires_at,
    )
    mem_id = await store.add(record)
    return JSONResponse(
        {"memory_id": mem_id, "created": True, "record": _record_payload(record)},
        status_code=201,
    )


@router.get("/{memory_id}")
async def get_memory(
    memory_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    r = await store.get(memory_id)
    if r is None:
        return json_error(404, "NOT_FOUND", f"memory {memory_id} 不存在或已过期")
    # 跨租户隔离校验
    if r.tenant_id != tenant.tenant_id and tenant.role != "platform_admin":
        return json_error(403, "FORBIDDEN", "跨租户访问禁止")
    return JSONResponse(_record_payload(r))


@router.post("/search")
async def search_memory(
    body: SearchRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    results = await store.search(
        tenant_id=tenant.tenant_id,
        scope=body.scope,
        scope_id=body.scope_id,
        query=body.query,
        top_k=body.top_k,
    )
    return JSONResponse(
        {
            "results": [_record_payload(r) for r in results],
            "count": len(results),
            "query": body.query,
            "scope": body.scope,
            "scope_id": body.scope_id,
        }
    )


@router.get("/list")
async def list_memory(
    scope: str,
    scope_id: str,
    limit: int = 100,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    records = await store.list_by_scope(
        tenant_id=tenant.tenant_id,
        scope=scope,
        scope_id=scope_id,
        limit=min(limit, 500),
    )
    return JSONResponse(
        {
            "records": [_record_payload(r) for r in records],
            "count": len(records),
            "scope": scope,
            "scope_id": scope_id,
        }
    )


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    # 跨租户校验：先 get
    r = await store.get(memory_id)
    if r is None:
        return json_error(404, "NOT_FOUND", f"memory {memory_id} 不存在")
    if r.tenant_id != tenant.tenant_id and tenant.role != "platform_admin":
        return json_error(403, "FORBIDDEN", "跨租户删除禁止")
    deleted = await store.delete(memory_id)
    return JSONResponse({"memory_id": memory_id, "deleted": deleted})
