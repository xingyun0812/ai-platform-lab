"""对象存储 REST API — Phase K #33

路由前缀：/internal/storage

接口：
    POST   /internal/storage/upload          上传文件（multipart）
    GET    /internal/storage/list            列出对象（?prefix=）
    GET    /internal/storage/config          查看当前后端配置（admin）
    POST   /internal/storage/presign/{key}   获取预签名 URL（admin）
    GET    /internal/storage/{key:path}      下载文件（StreamingResponse）
    DELETE /internal/storage/{key:path}      删除对象（admin）
    HEAD   /internal/storage/{key:path}      检查存在性 + 元数据（via GET /meta）
"""

from __future__ import annotations

import io
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/storage", tags=["storage"])


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


def _get_backend_or_503():
    try:
        from packages.storage.factory import get_storage
        backend = get_storage()
        if backend is None:
            return json_error(503, "STORAGE_DISABLED", "存储后端未初始化")
        return backend
    except Exception as e:
        return json_error(503, "STORAGE_ERROR", str(e))


def _mask_config(config: Any) -> dict:
    """返回配置信息，掩码敏感字段。"""
    return {
        "backend": config.backend,
        "bucket": config.bucket,
        "prefix": config.prefix,
        "region": config.region,
        "endpoint": config.endpoint,
        "access_key": "***" if config.access_key else None,
        "secret_key": "***" if config.secret_key else None,
        "local_root": str(config.local_root),
    }


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_file(
    file: UploadFile,
    key: str | None = Query(default=None, description="自定义对象 key，默认使用文件名"),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """上传文件到对象存储。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    if not file.filename and not key:
        return json_error(400, "INVALID_REQUEST", "需要提供文件名或 key 参数")

    object_key = key or (file.filename or "upload")
    data = await file.read()

    try:
        result_key = await backend.put(
            object_key,
            data,
            metadata={
                "tenant_id": tenant.tenant_id,
                "original_filename": file.filename or "",
                "content_type": file.content_type or "application/octet-stream",
            },
        )
        return JSONResponse(
            {
                "key": result_key,
                "size": len(data),
                "content_type": file.content_type,
            },
            status_code=201,
        )
    except Exception as e:
        return json_error(500, "UPLOAD_FAILED", str(e))


# ---------------------------------------------------------------------------
# 列出对象
# ---------------------------------------------------------------------------


@router.get("/list")
async def list_objects(
    prefix: str = Query(default="", description="对象 key 前缀过滤"),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """列出存储中的对象。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    try:
        keys = await backend.list(prefix=prefix)
        return JSONResponse({"objects": keys, "count": len(keys), "prefix": prefix})
    except Exception as e:
        return json_error(500, "LIST_FAILED", str(e))


# ---------------------------------------------------------------------------
# 查看后端配置（admin）
# ---------------------------------------------------------------------------


@router.get("/config")
async def get_config(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查看当前存储后端配置（掩码敏感字段），需要 admin 权限。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    err = _require_admin(tenant)
    if err is not None:
        return err

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    config = getattr(backend, "_config", None)
    if config is None:
        return json_error(500, "CONFIG_NOT_AVAILABLE", "后端未暴露配置信息")

    return JSONResponse({"config": _mask_config(config)})


# ---------------------------------------------------------------------------
# 预签名 URL（admin，仅 S3/OSS）
# ---------------------------------------------------------------------------


@router.post("/presign/{key:path}")
async def presign_url(
    key: str,
    expires: int = Query(default=3600, description="过期时间（秒）"),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """生成预签名下载 URL（S3/OSS 后端），需要 admin 权限。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    err = _require_admin(tenant)
    if err is not None:
        return err

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    if not hasattr(backend, "presign_get"):
        return json_error(501, "NOT_SUPPORTED", "当前存储后端不支持预签名 URL（仅 S3/OSS）")

    try:
        url = backend.presign_get(key, expires=expires)
        return JSONResponse({"key": key, "url": url, "expires_in": expires})
    except Exception as e:
        return json_error(500, "PRESIGN_FAILED", str(e))


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


@router.get("/{key:path}")
async def download_file(
    key: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> StreamingResponse | JSONResponse:
    """下载对象，返回字节流。"""
    # 拦截 list 和 config 路径（已被上面的路由处理）
    if key in ("list", "config"):
        return json_error(404, "NOT_FOUND", f"路由 {key} 不可通过此接口访问")

    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    try:
        data = await backend.get(key)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{key.split("/")[-1]}"'},
        )
    except FileNotFoundError:
        return json_error(404, "NOT_FOUND", f"对象 {key!r} 不存在")
    except Exception as e:
        return json_error(500, "DOWNLOAD_FAILED", str(e))


# ---------------------------------------------------------------------------
# 删除（admin）
# ---------------------------------------------------------------------------


@router.delete("/{key:path}")
async def delete_object(
    key: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """删除对象，需要 admin 权限。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    err = _require_admin(tenant)
    if err is not None:
        return err

    backend = _get_backend_or_503()
    if isinstance(backend, JSONResponse):
        return backend

    try:
        ok = await backend.delete(key)
        if not ok:
            return json_error(404, "NOT_FOUND", f"对象 {key!r} 不存在")
        return JSONResponse({"key": key, "deleted": True})
    except Exception as e:
        return json_error(500, "DELETE_FAILED", str(e))
