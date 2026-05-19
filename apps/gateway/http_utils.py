from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from apps.gateway.tenants import TenantRecord
from packages.contracts.errors import ErrorBody, ErrorDetail
from packages.observability.context import get_trace_id


def json_error(
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    tid = get_trace_id()
    body = ErrorBody(
        error=ErrorDetail(
            code=code,
            message=message,
            trace_id=tid,
            detail=detail,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def resolve_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="missing X-Tenant-Id")
    token = parse_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing Authorization Bearer")

    tenant = tenants.get(x_tenant_id.strip())
    if not tenant:
        raise HTTPException(status_code=401, detail="unknown tenant")

    if token != tenant.bearer_token:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    return tenant
