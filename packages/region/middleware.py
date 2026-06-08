from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.region.context import set_request_region
from packages.region.resolver import RegionViolation, resolve_region


def _resolve_tenant_for_region(request: Request) -> TenantRecord | None:
    x_tenant = request.headers.get("x-tenant-id")
    auth = request.headers.get("authorization")
    if not x_tenant or not auth:
        return None
    try:
        from apps.gateway.http_utils import resolve_tenant

        return resolve_tenant(x_tenant, auth, load_tenants())
    except Exception:
        return None


async def bind_region_context(request: Request) -> JSONResponse | None:
    """对 RAG 相关路径绑定 region → Qdrant URL；违规时返回 JSON 错误。"""
    path = request.url.path
    if not (path.startswith("/internal/") or path.startswith("/v1/rag/")):
        return None
    if path in ("/internal/providers/matrix", "/internal/regions"):
        return None

    tenant = _resolve_tenant_for_region(request)
    header_region = request.headers.get("x-region")
    try:
        if tenant is None:
            from packages.region.resolver import default_region_id, get_regions

            rid = (header_region or default_region_id()).strip()
            cfg = get_regions().get(rid)
            if cfg is None:
                return json_error(400, "REGION_UNKNOWN", f"未知 region: {rid}")
            set_request_region(region_id=cfg.region_id, qdrant_url=cfg.qdrant_url)
        else:
            cfg = resolve_region(
                header_region=header_region,
                tenant_home_region=tenant.home_region,
                tenant_data_zone=tenant.data_zone,
            )
            set_request_region(region_id=cfg.region_id, qdrant_url=cfg.qdrant_url)
    except RegionViolation as e:
        return json_error(403, e.code, e.message)
    return None
