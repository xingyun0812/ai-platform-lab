from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.audit.store import get_audit_store

router = APIRouter(prefix="/internal/audit", tags=["audit-internal"])


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


@router.get("/recent")
async def list_recent_audit(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    tenant_filter: Annotated[str | None, Query(alias="tenant_id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    settings = get_settings()
    if not settings.audit_enabled:
        return json_error(503, "AUDIT_DISABLED", "审计落库未启用")

    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if tenant.tenant_id != "admin":
        tenant_filter = tenant.tenant_id

    store = get_audit_store(settings.audit_db_path)
    records = store.recent(limit=limit, tenant_id=tenant_filter)
    return {
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at,
                "tenant_id": r.tenant_id,
                "method": r.method,
                "path": r.path,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "trace_id": r.trace_id,
                "model": r.model,
                "error_code": r.error_code,
            }
            for r in records
        ],
        "count": len(records),
    }
