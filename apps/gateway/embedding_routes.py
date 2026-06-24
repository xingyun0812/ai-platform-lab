"""Embedding 服务 REST API — Issue #35

路由前缀：/internal/embeddings

接口：
    GET    /internal/embeddings/models                  列出所有 embedding 模型
    GET    /internal/embeddings/models/{model_id}       获取模型详情
    POST   /internal/embeddings/models                  注册模型（admin）
    DELETE /internal/embeddings/models/{model_id}       删除模型（admin）
    POST   /internal/embeddings/embed                   生成 embedding
    GET    /internal/embeddings/cache/stats             缓存统计
    DELETE /internal/embeddings/cache                   清除缓存（admin）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/embeddings", tags=["embeddings"])


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


def _service_or_503():
    from packages.embedding.service import get_embedding_service

    svc = get_embedding_service()
    if svc is None:
        return json_error(503, "EMBEDDING_DISABLED", "EMBEDDING_SERVICE_ENABLED=false 或未初始化")
    return svc


class ModelCreateRequest(BaseModel):
    model_id: str = Field(..., min_length=1)
    name: str = ""
    provider: str = Field(default="stub", description="openai | stub | custom")
    dimensions: int = Field(default=1536, gt=0)
    max_input_tokens: int = Field(default=8192, gt=0)
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    metadata: dict = Field(default_factory=dict)


class EmbedRequest(BaseModel):
    model_id: str = Field(..., min_length=1)
    texts: list[str] | None = None
    inputs: list | None = None
    tenant_id: str = "system"


# --------------------------------------------------------------------- #
# Models CRUD
# --------------------------------------------------------------------- #


@router.get("/models")
async def list_models(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    models = svc._registry.list_models()
    return JSONResponse(
        {
            "models": [m.to_dict() for m in models],
            "stats": svc._registry.stats(),
        }
    )


@router.get("/models/{model_id}")
async def get_model(
    model_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    model = svc._registry.get_model(model_id)
    if model is None:
        return json_error(404, "NOT_FOUND", f"embedding model {model_id} 不存在")
    return JSONResponse(model.to_dict())


@router.post("/models")
async def create_model(
    body: ModelCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    if body.provider not in ("openai", "stub", "custom"):
        return json_error(400, "INVALID_PROVIDER", "provider 必须是 openai / stub / custom")
    existing = svc._registry.get_model(body.model_id)
    if existing is not None:
        return json_error(409, "ALREADY_EXISTS", f"embedding model {body.model_id} 已存在")
    from packages.embedding.models import EmbeddingModel

    model = EmbeddingModel(
        model_id=body.model_id,
        name=body.name or body.model_id,
        provider=body.provider,
        dimensions=body.dimensions,
        max_input_tokens=body.max_input_tokens,
        modalities=body.modalities or ["text"],
        metadata=body.metadata,
    )
    svc._registry.register_model(model)
    return JSONResponse(model.to_dict(), status_code=201)


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    ok = svc._registry.remove_model(model_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"embedding model {model_id} 不存在")
    return JSONResponse({"model_id": model_id, "deleted": True})


# --------------------------------------------------------------------- #
# Embed
# --------------------------------------------------------------------- #


@router.post("/embed")
async def embed(
    body: EmbedRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """生成 embedding。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    if not body.texts and not body.inputs:
        return json_error(400, "INVALID_INPUT", "texts 或 inputs 至少提供一个")
    from packages.embedding.models import EmbeddingRequest
    from packages.embedding.multimodal import MultimodalInputError

    req = EmbeddingRequest(
        model_id=body.model_id,
        texts=list(body.texts or []),
        inputs=body.inputs,
        tenant_id=body.tenant_id or getattr(tenant, "tenant_id", "system"),
    )
    try:
        resp = await svc.embed(req)
    except MultimodalInputError as e:
        return json_error(400, "INVALID_INPUT", str(e))
    except ValueError as e:
        return json_error(404, "MODEL_NOT_FOUND", str(e))
    except Exception as e:
        return json_error(500, "EMBED_FAILED", str(e))
    return JSONResponse(
        {
            "model_id": resp.model_id,
            "embeddings": resp.embeddings,
            "dimensions": resp.dimensions,
            "usage": resp.usage,
            "cached": resp.cached,
        }
    )


# --------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------- #


@router.get("/cache/stats")
async def cache_stats(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    return JSONResponse(svc.cache_stats())


@router.delete("/cache")
async def clear_cache(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    svc = _service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    cleared = svc.clear_cache()
    return JSONResponse({"cleared": cleared})
