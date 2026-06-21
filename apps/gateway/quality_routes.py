"""质量监控 REST API — Phase J #48

路由前缀：/internal/quality

接口：
    GET    /internal/quality/current/{tenant_id}       当前质量指标（query: window_seconds=300）
    GET    /internal/quality/trend/{tenant_id}         趋势（query: windows=12）
    GET    /internal/quality/alerts/{tenant_id}        当前告警
    POST   /internal/quality/alerts/check/{tenant_id}  触发告警检查（admin）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/quality", tags=["quality"])


# ─────────────────────────── helpers ─────────────────────────


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


def _metric_payload(m) -> dict[str, Any]:
    return {
        "tenant_id": m.tenant_id,
        "window_seconds": m.window_seconds,
        "total_requests": m.total_requests,
        "thumbs_up": m.thumbs_up,
        "thumbs_down": m.thumbs_down,
        "avg_rating": m.avg_rating,
        "bad_case_count": m.bad_case_count,
        "satisfaction_rate": m.satisfaction_rate,
        "timestamp": m.timestamp,
    }


def _alert_payload(a) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id,
        "tenant_id": a.tenant_id,
        "alert_type": a.alert_type,
        "threshold": a.threshold,
        "current_value": a.current_value,
        "message": a.message,
        "created_at": a.created_at,
        "severity": a.severity,
    }


# ─────────────────────────── Endpoints ───────────────────────


@router.get("/current/{tenant_id}")
async def get_current_metric(
    tenant_id: str,
    window_seconds: Annotated[int, Query(ge=10, le=86400)] = 300,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        from packages.quality_monitor.aggregator import get_quality_monitor
        agg = get_quality_monitor()
        if agg is None:
            return json_error(503, "QUALITY_MONITOR_DISABLED", "质量监控未初始化")
        metric = await agg.get_current(tenant_id, window_seconds=window_seconds)
        return JSONResponse(_metric_payload(metric))
    except Exception as exc:
        return json_error(500, "QUALITY_ERROR", str(exc))


@router.get("/trend/{tenant_id}")
async def get_trend(
    tenant_id: str,
    windows: Annotated[int, Query(ge=1, le=48)] = 12,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        from packages.quality_monitor.aggregator import get_quality_monitor
        agg = get_quality_monitor()
        if agg is None:
            return json_error(503, "QUALITY_MONITOR_DISABLED", "质量监控未初始化")
        metrics = await agg.get_trend(tenant_id, windows=windows)
        return JSONResponse({"trend": [_metric_payload(m) for m in metrics]})
    except Exception as exc:
        return json_error(500, "QUALITY_ERROR", str(exc))


@router.get("/alerts/{tenant_id}")
async def get_alerts(
    tenant_id: str,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        from packages.quality_monitor.alerts import AlertChecker
        checker = AlertChecker()
        alerts = await checker.run_all_checks(tenant_id)
        return JSONResponse({"alerts": [_alert_payload(a) for a in alerts], "total": len(alerts)})
    except Exception as exc:
        return json_error(500, "QUALITY_ERROR", str(exc))


@router.post("/alerts/check/{tenant_id}")
async def run_alert_checks(
    tenant_id: str,
    satisfaction_threshold: Annotated[float, Query(ge=0.0, le=1.0)] = 0.7,
    bad_case_threshold: Annotated[int, Query(ge=1)] = 10,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    err = _require_admin(tenant)
    if err:
        return err

    try:
        from packages.quality_monitor.alerts import AlertChecker
        checker = AlertChecker()
        alerts = await checker.run_all_checks(
            tenant_id,
            satisfaction_threshold=satisfaction_threshold,
            bad_case_threshold=bad_case_threshold,
        )
        return JSONResponse({"alerts": [_alert_payload(a) for a in alerts], "total": len(alerts)})
    except Exception as exc:
        return json_error(500, "QUALITY_ERROR", str(exc))
