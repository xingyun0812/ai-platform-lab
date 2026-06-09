from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.billing.budget import get_budget_snapshot
from packages.billing.cost import estimate_cost_usd
from packages.billing.db import get_billing_store

router = APIRouter(prefix="/internal/billing", tags=["billing-internal"])


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


@router.get("/usage")
async def billing_usage(
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
    tenant_filter: Annotated[str | None, Query(alias="tenant_id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    settings = get_settings()
    store = get_billing_store(settings.database_url)
    if store is None:
        return json_error(503, "BILLING_DISABLED", "DATABASE_URL 未配置或不可达")

    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if tenant.tenant_id != "admin":
        tenant_filter = tenant.tenant_id

    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = store.aggregate_by_tenant(since=since, tenant_id=tenant_filter)
    items = []
    for row in rows:
        tid = row["tenant_id"]
        tcfg = tenants.get(tid)
        snap = None
        if tcfg:
            snap = get_budget_snapshot(
                tid,
                token_budget_daily=tcfg.token_budget_daily,
                token_budget_monthly=tcfg.token_budget_monthly,
            )
        items.append(
            {
                **row,
                "budget": {
                    "token_budget_daily": tcfg.token_budget_daily if tcfg else -1,
                    "token_budget_monthly": tcfg.token_budget_monthly if tcfg else -1,
                    "used_tokens_daily": snap.used_daily if snap else None,
                    "used_tokens_monthly": snap.used_monthly if snap else None,
                    "remaining_daily": snap.remaining_daily if snap else None,
                    "remaining_monthly": snap.remaining_monthly if snap else None,
                }
                if tcfg
                else None,
            }
        )
    return {
        "since": since.isoformat(),
        "hours": hours,
        "items": items,
    }


@router.get("/export")
async def billing_export(
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
    tenant_filter: Annotated[str | None, Query(alias="tenant_id")] = None,
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "csv",
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    settings = get_settings()
    store = get_billing_store(settings.database_url)
    if store is None:
        return json_error(503, "BILLING_DISABLED", "DATABASE_URL 未配置或不可达")

    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if tenant.tenant_id != "admin":
        return json_error(403, "FORBIDDEN", "仅 admin 可导出账单")

    since = datetime.now(UTC) - timedelta(hours=hours)
    if format == "json":
        rows = store.recent_rows(limit=5000, tenant_id=tenant_filter)
        return {
            "since": since.isoformat(),
            "items": [
                {
                    "created_at": r.created_at,
                    "tenant_id": r.tenant_id,
                    "path": r.path,
                    "model": r.model,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "total_tokens": r.total_tokens,
                    "trace_id": r.trace_id,
                }
                for r in rows
            ],
        }

    csv_text = store.export_csv(since=since, tenant_id=tenant_filter)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=usage_export.csv"},
    )


@router.get("/invoice")
async def billing_invoice(
    month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    tenant_filter: Annotated[str | None, Query(alias="tenant_id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """按自然月汇总用量并估算 USD 成本（providers.yaml 单价）。"""
    settings = get_settings()
    store = get_billing_store(settings.database_url)
    if store is None:
        return json_error(503, "BILLING_DISABLED", "DATABASE_URL 未配置或不可达")

    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if tenant.tenant_id != "admin":
        tenant_filter = tenant.tenant_id

    year, mon = month.split("-")
    since = datetime(int(year), int(mon), 1, tzinfo=UTC)
    if int(mon) == 12:
        until = datetime(int(year) + 1, 1, 1, tzinfo=UTC)
    else:
        until = datetime(int(year), int(mon) + 1, 1, tzinfo=UTC)

    rows = store.recent_rows(limit=5000, tenant_id=tenant_filter)
    items: list[dict[str, Any]] = []
    total_usd = 0.0
    total_tokens = 0
    for row in rows:
        try:
            created_dt = datetime.fromisoformat(row.created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (since <= created_dt < until):
            continue
        cost = estimate_cost_usd(
            model=row.model,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
        )
        total_usd += cost
        total_tokens += row.total_tokens
        items.append(
            {
                "created_at": row.created_at,
                "tenant_id": row.tenant_id,
                "path": row.path,
                "model": row.model,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "total_tokens": row.total_tokens,
                "estimated_cost_usd": cost,
            }
        )

    return {
        "month": month,
        "tenant_id": tenant_filter,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(total_usd, 4),
        "line_items": items,
        "note": "成本为 providers.yaml 示意单价估算，非正式发票",
    }
