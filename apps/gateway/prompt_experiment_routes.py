"""Prompt A/B 实验 REST API — Phase F #30

路由前缀：/internal/prompts

接口：
    POST   /internal/prompts/{prompt_id}/experiments           创建实验（admin）
    GET    /internal/prompts/{prompt_id}/experiments           列出实验
    GET    /internal/prompts/{prompt_id}/experiments/current   当前运行中实验
    GET    /internal/prompts/{prompt_id}/experiments/{exp_id}  实验详情 + 指标
    POST   /internal/prompts/{prompt_id}/experiments/{exp_id}/stop      停止（admin）
    POST   /internal/prompts/{prompt_id}/experiments/{exp_id}/promote   提升胜出版本为 active（admin）
    POST   /internal/prompts/{prompt_id}/experiments/{exp_id}/feedback   记录质量反馈
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits
from packages.prompt import (
    ExperimentStore,
    ExperimentVariant,
    PromptRegistry,
    get_experiment_store,
    get_registry,
)

router = APIRouter(prefix="/internal/prompts", tags=["prompt-experiment"])


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


def _store() -> ExperimentStore | JSONResponse:
    s = get_experiment_store()
    if s is None:
        return json_error(503, "EXPERIMENT_DISABLED", "PROMPT_EXPERIMENT_ENABLED=false")
    return s


def _registry() -> PromptRegistry | JSONResponse:
    reg = get_registry()
    if reg is None:
        return json_error(503, "PROMPT_REGISTRY_DISABLED", "PROMPT_REGISTRY_ENABLED=false")
    return reg


class VariantSpec(BaseModel):
    version: int = Field(..., ge=1)
    percent: int = Field(..., ge=0, le=100)


class CreateExperimentRequest(BaseModel):
    variants: list[VariantSpec] = Field(..., min_length=2)
    min_samples: int = Field(default=100, ge=1)
    success_metric: str = Field(default="quality")  # quality | latency | tokens
    winner_margin: float = Field(default=0.1, ge=0.0, le=1.0)


class FeedbackRequest(BaseModel):
    version: int = Field(..., ge=1)
    score: float = Field(..., ge=0.0, le=1.0)


def _experiment_payload(exp, store: ExperimentStore) -> dict[str, Any]:
    metrics_map = store.all_metrics(exp.experiment_id)
    variants_out = []
    for v in exp.variants:
        m = metrics_map.get(v.version)
        variants_out.append(
            {
                "version": v.version,
                "percent": v.percent,
                "metrics": m.to_dict() if m else None,
            }
        )
    return {
        "experiment_id": exp.experiment_id,
        "prompt_id": exp.prompt_id,
        "tenant_id": exp.tenant_id,
        "status": exp.status,
        "success_metric": exp.success_metric,
        "winner_margin": exp.winner_margin,
        "winner_version": exp.winner_version,
        "min_samples": exp.min_samples,
        "created_at": exp.created_at,
        "stopped_at": exp.stopped_at,
        "created_by": exp.created_by,
        "variants": variants_out,
    }


@router.post("/{prompt_id}/experiments")
async def create_experiment(
    prompt_id: str,
    body: CreateExperimentRequest,
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
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    # 校验版本存在
    for v in body.variants:
        if reg.get_version(prompt_id, v.version) is None:
            return json_error(
                404,
                "VERSION_NOT_FOUND",
                f"prompt_id={prompt_id} version={v.version} 不存在",
            )
    try:
        exp = store.create_experiment(
            prompt_id=prompt_id,
            variants=[
                ExperimentVariant(version=v.version, percent=v.percent)
                for v in body.variants
            ],
            tenant_id="global",
            min_samples=body.min_samples,
            success_metric=body.success_metric,
            winner_margin=body.winner_margin,
            created_by=tenant.tenant_id,
        )
    except Exception as e:
        from packages.prompt import ExperimentError

        if isinstance(e, ExperimentError):
            return json_error(400, e.code, e.message)
        raise
    return JSONResponse(_experiment_payload(exp, store), status_code=201)


@router.get("/{prompt_id}/experiments")
async def list_experiments(
    prompt_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    exps = store.list_experiments(prompt_id=prompt_id)
    return JSONResponse(
        {
            "prompt_id": prompt_id,
            "experiments": [_experiment_payload(e, store) for e in exps],
            "count": len(exps),
            "stats": store.stats(),
        }
    )


@router.get("/{prompt_id}/experiments/current")
async def current_experiment(
    prompt_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    exp = store.get_running(prompt_id, tenant_id="global")
    if exp is None:
        return JSONResponse(
            {"prompt_id": prompt_id, "running": False, "experiment": None}
        )
    return JSONResponse(
        {
            "prompt_id": prompt_id,
            "running": True,
            "experiment": _experiment_payload(exp, store),
        }
    )


@router.get("/{prompt_id}/experiments/{exp_id}")
async def get_experiment(
    prompt_id: str,
    exp_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    exp = store.get_experiment(exp_id)
    if exp is None or exp.prompt_id != prompt_id:
        return json_error(404, "NOT_FOUND", f"experiment {exp_id} 不存在")
    # 顺便尝试自动胜出
    store.maybe_auto_winner(exp_id)
    exp = store.get_experiment(exp_id)  # 重取（可能已变 stopped）
    return JSONResponse(_experiment_payload(exp, store))


@router.post("/{prompt_id}/experiments/{exp_id}/stop")
async def stop_experiment(
    prompt_id: str,
    exp_id: str,
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
    try:
        exp = store.stop_experiment(exp_id)
    except Exception as e:
        from packages.prompt import ExperimentError

        if isinstance(e, ExperimentError):
            return json_error(400, e.code, e.message)
        raise
    return JSONResponse(_experiment_payload(exp, store))


@router.post("/{prompt_id}/experiments/{exp_id}/promote")
async def promote_experiment(
    prompt_id: str,
    exp_id: str,
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
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    try:
        winner_version = store.promote_winner(exp_id)
        # 调用 registry 切换 active
        v = reg.set_active(prompt_id, winner_version)
    except Exception as e:
        from packages.prompt import ExperimentError, PromptRegistryError

        if isinstance(e, ExperimentError):
            return json_error(400, e.code, e.message)
        if isinstance(e, PromptRegistryError):
            return json_error(404, e.code, e.message)
        raise
    return JSONResponse(
        {
            "experiment_id": exp_id,
            "promoted_version": winner_version,
            "active_version": v.version,
        }
    )


@router.post("/{prompt_id}/experiments/{exp_id}/feedback")
async def record_feedback(
    prompt_id: str,
    exp_id: str,
    body: FeedbackRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    store = _store()
    if isinstance(store, JSONResponse):
        return store
    exp = store.get_experiment(exp_id)
    if exp is None or exp.prompt_id != prompt_id:
        return json_error(404, "NOT_FOUND", f"experiment {exp_id} 不存在")
    try:
        store.record_quality(
            experiment_id=exp_id, version=body.version, score=body.score
        )
    except Exception as e:
        from packages.prompt import ExperimentError

        if isinstance(e, ExperimentError):
            return json_error(400, e.code, e.message)
        raise
    return JSONResponse(
        {
            "experiment_id": exp_id,
            "version": body.version,
            "score": body.score,
            "recorded": True,
        }
    )
