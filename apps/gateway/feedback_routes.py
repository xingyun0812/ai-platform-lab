"""反馈采集 REST API — Phase J #48

路由前缀：/internal/feedback

接口：
    POST   /internal/feedback/                    记录反馈（已认证）
    GET    /internal/feedback/{feedback_id}        获取单条反馈
    GET    /internal/feedback/                     列出反馈（query: tenant_id, feedback_type, limit）
    GET    /internal/feedback/bad-cases            列出差评（query: tenant_id, limit）
    GET    /internal/feedback/stats                统计（query: tenant_id）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/feedback", tags=["feedback"])


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


def _fb_payload(fb) -> dict[str, Any]:
    return {
        "feedback_id": fb.feedback_id,
        "tenant_id": fb.tenant_id,
        "session_id": fb.session_id,
        "message_id": fb.message_id,
        "feedback_type": fb.feedback_type,
        "rating": fb.rating,
        "comment": fb.comment,
        "user_id": fb.user_id,
        "created_at": fb.created_at,
        "metadata": fb.metadata,
    }


# ─────────────────────────── Request models ──────────────────


class RecordFeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message_id: str = Field(..., min_length=1)
    feedback_type: str = Field(..., description="thumbs_up|thumbs_down|rating_1-5|bad_case")
    rating: int | None = Field(None, ge=1, le=5)
    comment: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────── Endpoints ───────────────────────


@router.post("/")
async def record_feedback(
    body: RecordFeedbackRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        from packages.feedback.api import record_feedback as _record
        fb = await _record(
            tenant_id=tenant.tenant_id,
            session_id=body.session_id,
            message_id=body.message_id,
            feedback_type=body.feedback_type,
            rating=body.rating,
            comment=body.comment,
            user_id=body.user_id,
            metadata=body.metadata,
        )
        return JSONResponse(status_code=201, content=_fb_payload(fb))
    except Exception as exc:
        return json_error(500, "FEEDBACK_ERROR", str(exc))


@router.get("/bad-cases")
async def list_bad_cases(
    tenant_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    target_tenant = tenant_id or tenant.tenant_id
    err = _require_admin(tenant)
    if err:
        return err

    try:
        from packages.feedback.store import get_feedback_store
        store = get_feedback_store()
        if store is None:
            return json_error(503, "FEEDBACK_DISABLED", "反馈存储未初始化")
        items = await store.list_bad_cases(target_tenant, limit=limit)
        return JSONResponse({"bad_cases": [_fb_payload(fb) for fb in items], "total": len(items)})
    except Exception as exc:
        return json_error(500, "FEEDBACK_ERROR", str(exc))


@router.get("/stats")
async def feedback_stats(
    tenant_id: Annotated[str | None, Query()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    target_tenant = tenant_id or tenant.tenant_id

    try:
        from packages.feedback.store import get_feedback_store
        store = get_feedback_store()
        if store is None:
            return json_error(503, "FEEDBACK_DISABLED", "反馈存储未初始化")
        counts = await store.count_by_type(target_tenant)
        return JSONResponse({"tenant_id": target_tenant, "counts": counts})
    except Exception as exc:
        return json_error(500, "FEEDBACK_ERROR", str(exc))


@router.get("/{feedback_id}")
async def get_feedback(
    feedback_id: str,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    try:
        from packages.feedback.api import get_feedback as _get
        fb = await _get(feedback_id)
        if fb is None:
            return json_error(404, "NOT_FOUND", f"feedback {feedback_id} 不存在")
        return JSONResponse(_fb_payload(fb))
    except Exception as exc:
        return json_error(500, "FEEDBACK_ERROR", str(exc))


@router.get("/")
async def list_feedback(
    tenant_id: Annotated[str | None, Query()] = None,
    feedback_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    target_tenant = tenant_id or tenant.tenant_id

    try:
        from packages.feedback.api import list_feedback as _list
        items = await _list(target_tenant, feedback_type=feedback_type, limit=limit)
        return JSONResponse({"feedbacks": [_fb_payload(fb) for fb in items], "total": len(items)})
    except Exception as exc:
        return json_error(500, "FEEDBACK_ERROR", str(exc))
