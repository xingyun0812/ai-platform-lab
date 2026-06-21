from __future__ import annotations

"""OAuth2 provider — Issue #44: OAuth2 / mTLS

支持 Authorization Code Flow 与 Client Credentials Flow。
全局单例：init_oauth2_provider / get_oauth2_provider / reset_for_tests
使用 aiohttp 进行 HTTP 调用（可选依赖，未安装时降级提示）。
"""

import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuth2Config:
    """OAuth2 provider 配置。"""

    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    redirect_uri: str
    scopes: list[str]
    issuer: str


@dataclass(frozen=True)
class OAuth2Token:
    """OAuth2 token 响应。"""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str | None
    scope: str
    obtained_at: float = field(default_factory=time.time)

    def is_expired(self, buffer_seconds: int = 30) -> bool:
        """判断 token 是否已过期（含 buffer）。"""
        return (time.time() - self.obtained_at + buffer_seconds) >= self.expires_in


@dataclass
class OAuth2UserInfo:
    """来自 userinfo endpoint 的用户信息。"""

    sub: str
    email: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OAuth2Provider:
    """OAuth2 操作提供者（Authorization Code + Client Credentials）。"""

    def __init__(self, config: OAuth2Config) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Authorization Code Flow
    # ------------------------------------------------------------------

    def get_authorization_url(
        self,
        state: str,
        scopes: list[str] | None = None,
    ) -> str:
        """构造 Authorization URL（用于前端重定向）。"""
        scope_list = scopes if scopes is not None else self._config.scopes
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": " ".join(scope_list),
            "state": state,
        }
        return self._config.authorization_endpoint + "?" + urllib.parse.urlencode(params)

    async def exchange_code(self, code: str) -> OAuth2Token:
        """用授权码换取 token（Authorization Code Flow）。"""
        return await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.redirect_uri,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            }
        )

    async def client_credentials(self, scopes: list[str] | None = None) -> OAuth2Token:
        """Client Credentials Flow — 机器对机器鉴权。"""
        scope_list = scopes if scopes is not None else self._config.scopes
        return await self._post_token(
            {
                "grant_type": "client_credentials",
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
                "scope": " ".join(scope_list),
            }
        )

    async def refresh_token(self, refresh_token: str) -> OAuth2Token:
        """用 refresh_token 换取新的 access token。"""
        return await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            }
        )

    # ------------------------------------------------------------------
    # Token / userinfo validation
    # ------------------------------------------------------------------

    async def get_userinfo(self, access_token: str) -> OAuth2UserInfo:
        """通过 userinfo endpoint 获取用户信息。"""
        try:
            import aiohttp  # optional dependency
        except ImportError as exc:
            raise RuntimeError("aiohttp 未安装，无法调用 userinfo endpoint") from exc

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self._config.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"userinfo endpoint 返回 {resp.status}")
                data: dict[str, Any] = await resp.json()
                return self._parse_userinfo(data)

    async def verify_token(self, access_token: str) -> OAuth2UserInfo | None:
        """验证 access token 并返回用户信息；验证失败返回 None。"""
        try:
            return await self.get_userinfo(access_token)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_token(self, data: dict[str, str]) -> OAuth2Token:
        """向 token endpoint 发送 POST 请求。"""
        try:
            import aiohttp  # optional dependency
        except ImportError as exc:
            raise RuntimeError("aiohttp 未安装，无法调用 token endpoint") from exc

        obtained_at = time.time()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._config.token_endpoint,
                data=data,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise ValueError(f"token endpoint 返回 {resp.status}: {text}")
                payload: dict[str, Any] = await resp.json()
                return OAuth2Token(
                    access_token=payload.get("access_token", ""),
                    token_type=payload.get("token_type", "Bearer"),
                    expires_in=int(payload.get("expires_in", 3600)),
                    refresh_token=payload.get("refresh_token"),
                    scope=payload.get("scope", ""),
                    obtained_at=obtained_at,
                )

    @staticmethod
    def _parse_userinfo(data: dict[str, Any]) -> OAuth2UserInfo:
        roles_raw = data.get("roles", data.get("groups", []))
        if isinstance(roles_raw, str):
            roles_raw = [r.strip() for r in roles_raw.split(",") if r.strip()]
        return OAuth2UserInfo(
            sub=str(data.get("sub", "")),
            email=data.get("email"),
            name=data.get("name"),
            roles=list(roles_raw) if isinstance(roles_raw, list) else [],
            metadata={k: v for k, v in data.items() if k not in ("sub", "email", "name", "roles", "groups")},
        )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_provider: OAuth2Provider | None = None


def init_oauth2_provider(config: OAuth2Config) -> OAuth2Provider:
    """初始化全局 OAuth2Provider（幂等）。"""
    global _provider
    with _lock:
        _provider = OAuth2Provider(config)
        return _provider


def get_oauth2_provider() -> OAuth2Provider | None:
    """获取全局 OAuth2Provider；未初始化时返回 None。"""
    return _provider


def reset_for_tests() -> None:
    """重置单例（仅供测试使用）。"""
    global _provider
    with _lock:
        _provider = None
