from __future__ import annotations

"""Auth 中间件 — Issue #44: OAuth2 / mTLS

OAuth2Middleware:
    - OAUTH2_ENABLED=false 时直接透传（现有 JWT HS256 鉴权不受影响）
    - 启用时从 Authorization: Bearer 提取 token 并调用 OAuth2Provider.verify_token()
    - OAuth2 验证失败且 JWT_FALLBACK=true 时回退到 JWT HS256

mTLSAuthDependency (FastAPI Depends):
    - MTLS_ENABLED=true 时校验客户端证书并提取身份
    - 禁用时透传
"""

from dataclasses import dataclass
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from packages.auth.oauth2 import OAuth2UserInfo, get_oauth2_provider


# ---------------------------------------------------------------------------
# OAuth2 Middleware
# ---------------------------------------------------------------------------


class OAuth2Middleware(BaseHTTPMiddleware):
    """Starlette/FastAPI 中间件：OAuth2 Bearer token 验证。

    配置通过注入 settings 对象（或字典）传入，避免循环导入。
    """

    def __init__(
        self,
        app,
        *,
        oauth2_enabled: bool = False,
        jwt_fallback: bool = True,
        jwt_secret: str | None = None,
    ) -> None:
        super().__init__(app)
        self._oauth2_enabled = oauth2_enabled
        self._jwt_fallback = jwt_fallback
        self._jwt_secret = jwt_secret

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._oauth2_enabled:
            # 功能关闭，直接透传
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        token = _extract_bearer(authorization)

        if token:
            provider = get_oauth2_provider()
            user_info: OAuth2UserInfo | None = None

            if provider is not None:
                user_info = await provider.verify_token(token)

            if user_info is None and self._jwt_fallback and self._jwt_secret:
                # 回退到 JWT HS256
                user_info = _jwt_fallback_verify(token, self._jwt_secret)

            if user_info is not None:
                request.state.oauth2_user = user_info

        return await call_next(request)


def _extract_bearer(authorization: str) -> str | None:
    """从 Authorization header 提取 Bearer token。"""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _jwt_fallback_verify(token: str, secret: str) -> OAuth2UserInfo | None:
    """JWT HS256 回退验证，返回 OAuth2UserInfo 格式。"""
    try:
        from packages.auth.jwt_hs256 import decode_hs256

        claims = decode_hs256(token, secret)
        if not claims:
            return None
        return OAuth2UserInfo(
            sub=str(claims.get("sub", claims.get("tenant_id", ""))),
            email=claims.get("email"),
            name=claims.get("name"),
            roles=[claims["role"]] if isinstance(claims.get("role"), str) else [],
            metadata={k: v for k, v in claims.items() if k not in ("sub", "email", "name", "role")},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# mTLS FastAPI Dependency
# ---------------------------------------------------------------------------


@dataclass
class MTLSIdentity:
    """mTLS 提取的客户端身份信息。"""

    client_id: str | None
    cert_present: bool
    verified: bool


async def mtls_auth_dependency(
    request: Request,
    mtls_enabled: bool = False,
) -> MTLSIdentity:
    """FastAPI Depends 工厂：mTLS 客户端证书验证。

    MTLS_ENABLED=false 时直接通过（返回 MTLSIdentity(cert_present=False, verified=False)）。
    """
    if not mtls_enabled:
        return MTLSIdentity(client_id=None, cert_present=False, verified=False)

    from packages.auth.mtls import get_mtls_context

    ctx = get_mtls_context()
    if ctx is None:
        return MTLSIdentity(client_id=None, cert_present=False, verified=False)

    # 尝试从 request scope 中获取 TLS peer cert（需要 uvicorn mTLS 配置）
    ssl_object = request.scope.get("ssl")
    cert = None
    if ssl_object is not None:
        try:
            cert = ssl_object.getpeercert()
        except Exception:
            pass

    if cert is None:
        return MTLSIdentity(client_id=None, cert_present=False, verified=False)

    client_id = ctx.extract_client_identity(cert)
    return MTLSIdentity(client_id=client_id, cert_present=True, verified=True)


def make_mtls_dependency(mtls_enabled: bool = False) -> Callable:
    """创建已绑定 mtls_enabled 标志的 FastAPI Depends 工厂。"""

    async def _dep(request: Request) -> MTLSIdentity:
        return await mtls_auth_dependency(request, mtls_enabled=mtls_enabled)

    return _dep
