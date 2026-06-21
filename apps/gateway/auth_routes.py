"""OAuth2 / mTLS 管理 REST API — Issue #44

路由前缀：/internal/auth

接口：
    GET    /internal/auth/oauth2/authorize     重定向到 OAuth2 授权页面
    POST   /internal/auth/oauth2/callback      处理授权码回调 → 换取 token
    POST   /internal/auth/oauth2/refresh       刷新 access token
    GET    /internal/auth/oauth2/userinfo      获取当前用户信息
    GET    /internal/auth/mtls/status          mTLS 配置状态
    GET    /internal/auth/config               查看已启用的鉴权方式（admin）
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(x_tenant_id: str | None, authorization: str | None):
    tenants = load_tenants()
    try:
        from fastapi import HTTPException

        return resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as e:
        from fastapi import HTTPException

        if isinstance(e, HTTPException):
            return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))
        return json_error(401, "UNAUTHORIZED", str(e))


def _require_admin(tenant):
    if not can_patch_tenant_limits(tenant.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _get_settings():
    from apps.gateway.settings import get_settings

    return get_settings()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CallbackRequest(BaseModel):
    code: str = Field(..., min_length=1, description="OAuth2 授权码")
    state: str = Field(default="", description="防 CSRF state 参数")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# OAuth2 endpoints
# ---------------------------------------------------------------------------


@router.get("/oauth2/authorize")
async def oauth2_authorize(
    state: str = "",
    scope: str = "",
) -> Any:
    """构造 OAuth2 授权 URL 并重定向。

    Query params:
      - state: 防 CSRF（可选，不传则自动生成）
      - scope: 空格分隔的 scope（可选，默认使用配置值）
    """
    settings = _get_settings()
    if not getattr(settings, "oauth2_enabled", False):
        return json_error(503, "OAUTH2_DISABLED", "OAUTH2_ENABLED=false")

    from packages.auth.oauth2 import get_oauth2_provider

    provider = get_oauth2_provider()
    if provider is None:
        return json_error(503, "OAUTH2_NOT_INITIALIZED", "OAuth2 provider 未初始化")

    state = state or secrets.token_urlsafe(16)
    scopes = [s.strip() for s in scope.split() if s.strip()] if scope else None
    url = provider.get_authorization_url(state=state, scopes=scopes)
    return RedirectResponse(url=url, status_code=302)


@router.post("/oauth2/callback")
async def oauth2_callback(
    body: CallbackRequest,
) -> JSONResponse:
    """授权码 → 换取 access token。"""
    settings = _get_settings()
    if not getattr(settings, "oauth2_enabled", False):
        return json_error(503, "OAUTH2_DISABLED", "OAUTH2_ENABLED=false")

    from packages.auth.oauth2 import get_oauth2_provider

    provider = get_oauth2_provider()
    if provider is None:
        return json_error(503, "OAUTH2_NOT_INITIALIZED", "OAuth2 provider 未初始化")

    try:
        token = await provider.exchange_code(body.code)
        return JSONResponse(
            {
                "access_token": token.access_token,
                "token_type": token.token_type,
                "expires_in": token.expires_in,
                "refresh_token": token.refresh_token,
                "scope": token.scope,
                "obtained_at": token.obtained_at,
            }
        )
    except Exception as exc:
        return json_error(400, "TOKEN_EXCHANGE_FAILED", str(exc))


@router.post("/oauth2/refresh")
async def oauth2_refresh(
    body: RefreshRequest,
) -> JSONResponse:
    """用 refresh_token 换取新的 access token。"""
    settings = _get_settings()
    if not getattr(settings, "oauth2_enabled", False):
        return json_error(503, "OAUTH2_DISABLED", "OAUTH2_ENABLED=false")

    from packages.auth.oauth2 import get_oauth2_provider

    provider = get_oauth2_provider()
    if provider is None:
        return json_error(503, "OAUTH2_NOT_INITIALIZED", "OAuth2 provider 未初始化")

    try:
        token = await provider.refresh_token(body.refresh_token)
        return JSONResponse(
            {
                "access_token": token.access_token,
                "token_type": token.token_type,
                "expires_in": token.expires_in,
                "refresh_token": token.refresh_token,
                "scope": token.scope,
                "obtained_at": token.obtained_at,
            }
        )
    except Exception as exc:
        return json_error(400, "TOKEN_REFRESH_FAILED", str(exc))


@router.get("/oauth2/userinfo")
async def oauth2_userinfo(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """用 Bearer token 查询当前用户的 userinfo。"""
    settings = _get_settings()
    if not getattr(settings, "oauth2_enabled", False):
        return json_error(503, "OAUTH2_DISABLED", "OAUTH2_ENABLED=false")

    if not authorization:
        return json_error(401, "MISSING_TOKEN", "缺少 Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return json_error(401, "INVALID_TOKEN_FORMAT", "需要 Bearer token")

    access_token = parts[1]

    from packages.auth.oauth2 import get_oauth2_provider

    provider = get_oauth2_provider()
    if provider is None:
        return json_error(503, "OAUTH2_NOT_INITIALIZED", "OAuth2 provider 未初始化")

    try:
        user_info = await provider.get_userinfo(access_token)
        return JSONResponse(
            {
                "sub": user_info.sub,
                "email": user_info.email,
                "name": user_info.name,
                "roles": user_info.roles,
                "metadata": user_info.metadata,
            }
        )
    except Exception as exc:
        return json_error(401, "USERINFO_FAILED", str(exc))


# ---------------------------------------------------------------------------
# mTLS endpoints
# ---------------------------------------------------------------------------


@router.get("/mtls/status")
async def mtls_status(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查询 mTLS 配置状态。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    settings = _get_settings()
    mtls_enabled = getattr(settings, "mtls_enabled", False)

    result: dict[str, Any] = {
        "mtls_enabled": mtls_enabled,
    }

    if mtls_enabled:
        from packages.auth.mtls import get_mtls_context

        ctx = get_mtls_context()
        result["initialized"] = ctx is not None
        if ctx is not None:
            cfg = ctx._config
            result["client_cert_required"] = cfg.client_cert_required
            result["verify_client"] = cfg.verify_client
            result["ca_cert_configured"] = bool(cfg.ca_cert_path)
            result["server_cert_configured"] = bool(cfg.server_cert_path)

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Config endpoint (admin)
# ---------------------------------------------------------------------------


@router.get("/config")
async def auth_config(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """查看当前启用的鉴权方式（需要 platform_admin）。"""
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant

    err = _require_admin(tenant)
    if err is not None:
        return err

    settings = _get_settings()

    return JSONResponse(
        {
            "jwt_hs256": {
                "enabled": getattr(settings, "auth_jwt_enabled", True),
                "description": "HMAC-SHA256 JWT（默认鉴权方式）",
            },
            "oauth2": {
                "enabled": getattr(settings, "oauth2_enabled", False),
                "client_id": getattr(settings, "oauth2_client_id", None),
                "issuer": getattr(settings, "oauth2_issuer", None),
                "jwt_fallback": getattr(settings, "oauth2_jwt_fallback", True),
            },
            "mtls": {
                "enabled": getattr(settings, "mtls_enabled", False),
                "client_cert_required": getattr(settings, "mtls_client_cert_required", True),
            },
        }
    )
