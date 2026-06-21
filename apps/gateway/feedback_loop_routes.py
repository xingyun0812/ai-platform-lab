"""反馈飞轮 REST API — Phase J #48

路由前缀：/internal/feedback-loop

接口：
    POST   /internal/feedback-loop/collect/{tenant_id}        收集差评（admin）
    POST   /internal/feedback-loop/ingest                     入库 eval（admin）
    POST   /internal/feedback-loop/suggest/{prompt_id}        生成 Prompt 建议（admin）
    POST   /internal/feedback-loop/experiment/{suggestion_id} 自动创建 A/B 实验（admin）
    POST   /internal/feedback-loop/cycle/{tenant_id}          完整飞轮（admin）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/feedback-loop", tags=["feedback_loop"])


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


def _loop_or_503():
    from packages.feedback_loop.pipeline import get_feedback_loop
    loop = get_feedback_loop()
    if loop is None:
        return json_error(503, "FEEDBACK_LOOP_DISABLED", "反馈飞轮未初始化")
    return loop


def _suggestion_payload(s) -> dict[str, Any]:
    return {
        "suggestion_id": s.suggestion_id,
        "prompt_id": s.prompt_id,
        "current_version": s.current_version,
        "suggested_changes": s.suggested_changes,
        "reasoning": s.reasoning,
        "expected_impact": s.expected_impact,
        "bad_case_ids": s.bad_case_ids,
        "created_at": s.created_at,
        "status": s.status,
    }


# ─────────────────────────── Request models ──────────────────


class IngestRequest(BaseModel):
    bad_case_ids: list[str] = Field(..., min_length=1)


class SuggestRequest(BaseModel):
    bad_case_ids: list[str] = Field(..., min_length=1)


class CycleRequest(BaseModel):
    prompt_id: str = Field(..., min_length=1)


# ─────────────────────────── Endpoints ───────────────────────


@router.post("/collect/{tenant_id}")
async def collect_bad_cases(
    tenant_id: str,
    since: float | None = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err:
        return err

    loop = _loop_or_503()
    if isinstance(loop, JSONResponse):
        return loop

    try:
        bad_cases = await loop.collect_bad_cases(tenant_id, since=since)
        return JSONResponse({
            "tenant_id": tenant_id,
            "collected": len(bad_cases),
            "bad_case_ids": [bc.feedback_id for bc in bad_cases],
        })
    except Exception as exc:
        return json_error(500, "FEEDBACK_LOOP_ERROR", str(exc))


@router.post("/ingest")
async def ingest_bad_cases(
    body: IngestRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err:
        return err

    loop = _loop_or_503()
    if isinstance(loop, JSONResponse):
        return loop

    try:
        # 从 store 拉取指定 IDs
        from packages.feedback.store import get_feedback_store
        store = get_feedback_store()
        if store is None:
            return json_error(503, "FEEDBACK_DISABLED", "反馈存储未初始化")
        bad_cases = []
        for bid in body.bad_case_ids:
            fb = await store.get(bid)
            if fb:
                bad_cases.append(fb)
        count = await loop.ingest_to_eval(bad_cases)
        return JSONResponse({"ingested_count": count, "requested_ids": body.bad_case_ids})
    except Exception as exc:
        return json_error(500, "FEEDBACK_LOOP_ERROR", str(exc))


@router.post("/suggest/{prompt_id}")
async def generate_suggestion(
    prompt_id: str,
    body: SuggestRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err:
        return err

    loop = _loop_or_503()
    if isinstance(loop, JSONResponse):
        return loop

    try:
        from packages.feedback.store import get_feedback_store
        store = get_feedback_store()
        if store is None:
            return json_error(503, "FEEDBACK_DISABLED", "反馈存储未初始化")
        bad_cases = []
        for bid in body.bad_case_ids:
            fb = await store.get(bid)
            if fb:
                bad_cases.append(fb)
        suggestion = await loop.generate_prompt_suggestion(prompt_id, bad_cases)
        return JSONResponse(status_code=201, content=_suggestion_payload(suggestion))
    except Exception as exc:
        return json_error(500, "FEEDBACK_LOOP_ERROR", str(exc))


@router.post("/experiment/{suggestion_id}")
async def create_experiment(
    suggestion_id: str,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err:
        return err

    loop = _loop_or_503()
    if isinstance(loop, JSONResponse):
        return loop

    try:
        suggestion = loop.get_suggestion(suggestion_id)
        if suggestion is None:
            return json_error(404, "NOT_FOUND", f"suggestion {suggestion_id} 不存在")
        experiment_id = await loop.auto_create_experiment(suggestion)
        return JSONResponse({
            "suggestion_id": suggestion_id,
            "experiment_id": experiment_id,
            "status": suggestion.status,
        })
    except Exception as exc:
        return json_error(500, "FEEDBACK_LOOP_ERROR", str(exc))


@router.post("/cycle/{tenant_id}")
async def run_full_cycle(
    tenant_id: str,
    body: CycleRequest,
    x_tenant_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err:
        return err

    loop = _loop_or_503()
    if isinstance(loop, JSONResponse):
        return loop

    try:
        result = await loop.run_full_cycle(tenant_id, body.prompt_id)
        return JSONResponse(result)
    except Exception as exc:
        return json_error(500, "FEEDBACK_LOOP_ERROR", str(exc))
