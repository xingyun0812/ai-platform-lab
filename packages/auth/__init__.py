from __future__ import annotations

"""packages/auth — 鉴权模块

提供：
  - JWT HS256 (jwt_hs256.py)
  - RBAC (rbac.py)
  - OAuth2 + Client Credentials (oauth2.py)  [Issue #44]
  - mTLS (mtls.py)                           [Issue #44]
  - Middleware / Depends (middleware.py)      [Issue #44]
"""

from packages.auth.jwt_hs256 import decode_hs256
from packages.auth.mtls import (
    MTLSConfig,
    MTLSContext,
    get_mtls_context,
    init_mtls_context,
)
from packages.auth.oauth2 import (
    OAuth2Config,
    OAuth2Provider,
    OAuth2Token,
    OAuth2UserInfo,
    get_oauth2_provider,
    init_oauth2_provider,
)
from packages.auth.rbac import (
    can_approve_tools,
    can_patch_tenant_limits,
    can_view_tenant_profile,
    role_at_least,
)

__all__ = [
    # JWT
    "decode_hs256",
    # RBAC
    "role_at_least",
    "can_patch_tenant_limits",
    "can_approve_tools",
    "can_view_tenant_profile",
    # OAuth2
    "OAuth2Config",
    "OAuth2Token",
    "OAuth2UserInfo",
    "OAuth2Provider",
    "init_oauth2_provider",
    "get_oauth2_provider",
    # mTLS
    "MTLSConfig",
    "MTLSContext",
    "init_mtls_context",
    "get_mtls_context",
]
