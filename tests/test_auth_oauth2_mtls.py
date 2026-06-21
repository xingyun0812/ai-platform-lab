#!/usr/bin/env python3
"""OAuth2 / mTLS 单元测试 — Issue #44

运行：
    python3 tests/test_auth_oauth2_mtls.py

通过 importlib.util 直接加载模块，避免触发 packages.agent.__init__ 的 pydantic 链。
所有测试均兼容 Python 3.9+。
"""

from __future__ import annotations

import asyncio
import importlib.util
import ssl
import sys
import time
from pathlib import Path
from types import ModuleType

# ---------------------------------------------------------------------------
# Repo root 注入
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Module loader helpers
# ---------------------------------------------------------------------------


def _load_module(rel_path: str, name: str) -> ModuleType:
    """按文件路径加载 Python 模块并注册到 sys.modules。"""
    full_path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, str(full_path))
    assert spec is not None and spec.loader is not None, f"无法加载 {rel_path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# 预先加载依赖
_load_module("packages/auth/jwt_hs256.py", "packages.auth.jwt_hs256")
_load_module("packages/auth/rbac.py", "packages.auth.rbac")

oauth2_mod = _load_module("packages/auth/oauth2.py", "packages.auth.oauth2")
mtls_mod = _load_module("packages/auth/mtls.py", "packages.auth.mtls")
middleware_mod = _load_module("packages/auth/middleware.py", "packages.auth.middleware")

# Shorthand aliases
OAuth2Config = oauth2_mod.OAuth2Config
OAuth2Token = oauth2_mod.OAuth2Token
OAuth2UserInfo = oauth2_mod.OAuth2UserInfo
OAuth2Provider = oauth2_mod.OAuth2Provider
init_oauth2_provider = oauth2_mod.init_oauth2_provider
get_oauth2_provider = oauth2_mod.get_oauth2_provider
reset_oauth2 = oauth2_mod.reset_for_tests

MTLSConfig = mtls_mod.MTLSConfig
MTLSContext = mtls_mod.MTLSContext
init_mtls_context = mtls_mod.init_mtls_context
get_mtls_context = mtls_mod.get_mtls_context
reset_mtls = mtls_mod.reset_for_tests


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


def _run_async(coro):
    """兼容 Python 3.9 的 async 测试运行器。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_oauth2_config_frozen():
    """OAuth2Config 是 frozen dataclass，属性不可变。"""
    cfg = OAuth2Config(
        client_id="client-123",
        client_secret="secret-abc",
        authorization_endpoint="https://idp.example.com/auth",
        token_endpoint="https://idp.example.com/token",
        userinfo_endpoint="https://idp.example.com/userinfo",
        redirect_uri="https://app.example.com/callback",
        scopes=["openid", "profile", "email"],
        issuer="https://idp.example.com",
    )
    assert cfg.client_id == "client-123"
    assert cfg.scopes == ["openid", "profile", "email"]
    try:
        cfg.client_id = "other"  # type: ignore[misc]
        assert False, "frozen dataclass 应不可修改"
    except (AttributeError, TypeError):
        pass
    print("PASS test_oauth2_config_frozen")


def test_oauth2_token_fields():
    """OAuth2Token 字段正确存储。"""
    t = OAuth2Token(
        access_token="tok-abc",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="ref-xyz",
        scope="openid profile",
        obtained_at=time.time(),
    )
    assert t.access_token == "tok-abc"
    assert t.token_type == "Bearer"
    assert t.expires_in == 3600
    assert t.refresh_token == "ref-xyz"
    print("PASS test_oauth2_token_fields")


def test_oauth2_token_expiry():
    """OAuth2Token.is_expired() 逻辑正确。"""
    # 刚获取的 token（expires_in=3600）不应过期
    t = OAuth2Token(
        access_token="x",
        token_type="Bearer",
        expires_in=3600,
        refresh_token=None,
        scope="",
        obtained_at=time.time(),
    )
    assert not t.is_expired(), "刚获取的 token 不应过期"

    # 已过期的 token
    t_expired = OAuth2Token(
        access_token="x",
        token_type="Bearer",
        expires_in=1,
        refresh_token=None,
        scope="",
        obtained_at=time.time() - 100,  # 100 秒前获取
    )
    assert t_expired.is_expired(), "早已过期的 token 应返回 True"
    print("PASS test_oauth2_token_expiry")


def test_oauth2_userinfo_defaults():
    """OAuth2UserInfo 默认值正确。"""
    u = OAuth2UserInfo(sub="user-001")
    assert u.sub == "user-001"
    assert u.email is None
    assert u.name is None
    assert u.roles == []
    assert u.metadata == {}
    print("PASS test_oauth2_userinfo_defaults")


def test_oauth2_userinfo_full():
    """OAuth2UserInfo 完整字段。"""
    u = OAuth2UserInfo(
        sub="user-002",
        email="alice@example.com",
        name="Alice",
        roles=["admin", "developer"],
        metadata={"department": "eng"},
    )
    assert u.email == "alice@example.com"
    assert "admin" in u.roles
    assert u.metadata["department"] == "eng"
    print("PASS test_oauth2_userinfo_full")


def test_oauth2_provider_authorization_url():
    """OAuth2Provider.get_authorization_url() 构造正确的 URL。"""
    reset_oauth2()
    cfg = OAuth2Config(
        client_id="my-client",
        client_secret="my-secret",
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        userinfo_endpoint="https://idp.example.com/userinfo",
        redirect_uri="https://app.example.com/callback",
        scopes=["openid", "email"],
        issuer="https://idp.example.com",
    )
    provider = OAuth2Provider(cfg)
    url = provider.get_authorization_url(state="my-state")
    assert "response_type=code" in url
    assert "client_id=my-client" in url
    assert "state=my-state" in url
    assert "openid" in url
    assert url.startswith("https://idp.example.com/authorize?")
    print("PASS test_oauth2_provider_authorization_url")


def test_oauth2_provider_authorization_url_custom_scopes():
    """get_authorization_url() 支持自定义 scopes 覆盖默认值。"""
    cfg = OAuth2Config(
        client_id="c",
        client_secret="s",
        authorization_endpoint="https://idp.example.com/authorize",
        token_endpoint="https://idp.example.com/token",
        userinfo_endpoint="https://idp.example.com/userinfo",
        redirect_uri="https://app.example.com/callback",
        scopes=["openid"],
        issuer="https://idp.example.com",
    )
    provider = OAuth2Provider(cfg)
    url = provider.get_authorization_url(state="s1", scopes=["profile", "admin"])
    assert "profile" in url
    assert "admin" in url
    print("PASS test_oauth2_provider_authorization_url_custom_scopes")


def test_oauth2_singleton():
    """全局单例 init / get / reset 工作正常。"""
    reset_oauth2()
    assert get_oauth2_provider() is None

    cfg = OAuth2Config(
        client_id="c",
        client_secret="s",
        authorization_endpoint="https://a.com/auth",
        token_endpoint="https://a.com/token",
        userinfo_endpoint="https://a.com/userinfo",
        redirect_uri="https://a.com/cb",
        scopes=["openid"],
        issuer="https://a.com",
    )
    p1 = init_oauth2_provider(cfg)
    p2 = get_oauth2_provider()
    assert p1 is p2

    reset_oauth2()
    assert get_oauth2_provider() is None
    print("PASS test_oauth2_singleton")


def test_mtls_config_defaults():
    """MTLSConfig 默认值正确。"""
    cfg = MTLSConfig()
    assert cfg.enabled is False
    assert cfg.ca_cert_path == ""
    assert cfg.server_cert_path == ""
    assert cfg.client_cert_required is True
    assert cfg.verify_client is True
    assert cfg.allowed_fingerprints == []
    print("PASS test_mtls_config_defaults")


def test_mtls_config_custom():
    """MTLSConfig 支持自定义配置。"""
    cfg = MTLSConfig(
        enabled=True,
        ca_cert_path="/etc/certs/ca.pem",
        server_cert_path="/etc/certs/server.crt",
        server_key_path="/etc/certs/server.key",
        client_cert_required=False,
        verify_client=False,
    )
    assert cfg.enabled is True
    assert cfg.ca_cert_path == "/etc/certs/ca.pem"
    assert cfg.client_cert_required is False
    print("PASS test_mtls_config_custom")


def test_mtls_context_ssl_context_no_certs():
    """MTLSContext.get_ssl_context() 在无证书路径时创建基础 SSLContext。"""
    cfg = MTLSConfig(enabled=True)
    ctx = MTLSContext(cfg)
    ssl_ctx = ctx.get_ssl_context()
    assert isinstance(ssl_ctx, ssl.SSLContext)
    print("PASS test_mtls_context_ssl_context_no_certs")


def test_mtls_context_extract_identity_dict():
    """extract_client_identity() 从 dict 格式 cert 中提取 CN。"""
    cfg = MTLSConfig()
    ctx = MTLSContext(cfg)
    cert_dict = {
        "subject": [
            [("countryName", "CN")],
            [("organizationName", "Acme Corp")],
            [("commonName", "client-service-A")],
        ]
    }
    identity = ctx.extract_client_identity(cert_dict)
    assert identity == "client-service-A", f"期望 client-service-A，得到 {identity}"
    print("PASS test_mtls_context_extract_identity_dict")


def test_mtls_context_extract_identity_none():
    """extract_client_identity() 对 None 返回 None。"""
    cfg = MTLSConfig()
    ctx = MTLSContext(cfg)
    assert ctx.extract_client_identity(None) is None
    print("PASS test_mtls_context_extract_identity_none")


def test_mtls_singleton():
    """mTLS 全局单例 init / get / reset 工作正常。"""
    reset_mtls()
    assert get_mtls_context() is None

    cfg = MTLSConfig(enabled=True)
    c1 = init_mtls_context(cfg)
    c2 = get_mtls_context()
    assert c1 is c2

    reset_mtls()
    assert get_mtls_context() is None
    print("PASS test_mtls_singleton")


def test_oauth2_parse_userinfo_with_roles():
    """_parse_userinfo 正确解析 roles 字段。"""
    data = {
        "sub": "u123",
        "email": "bob@example.com",
        "name": "Bob",
        "roles": ["viewer", "developer"],
        "extra_field": "extra_value",
    }
    user = OAuth2Provider._parse_userinfo(data)
    assert user.sub == "u123"
    assert user.email == "bob@example.com"
    assert "viewer" in user.roles
    assert "developer" in user.roles
    assert user.metadata.get("extra_field") == "extra_value"
    print("PASS test_oauth2_parse_userinfo_with_roles")


def test_oauth2_parse_userinfo_string_roles():
    """_parse_userinfo 支持逗号分隔的 roles 字符串。"""
    data = {
        "sub": "u456",
        "roles": "admin,developer,viewer",
    }
    user = OAuth2Provider._parse_userinfo(data)
    assert "admin" in user.roles
    assert "developer" in user.roles
    assert "viewer" in user.roles
    print("PASS test_oauth2_parse_userinfo_string_roles")


def test_mtls_verify_client_cert_no_whitelist():
    """verify_client_cert() 白名单为空时接受非空 PEM。"""
    cfg = MTLSConfig(verify_client=True, allowed_fingerprints=[])
    ctx = MTLSContext(cfg)
    fake_pem = "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"
    # 白名单为空，任意非空 cert 应通过
    assert ctx.verify_client_cert(fake_pem) is True
    print("PASS test_mtls_verify_client_cert_no_whitelist")


def test_mtls_verify_client_cert_disabled():
    """verify_client=False 时直接通过。"""
    cfg = MTLSConfig(verify_client=False)
    ctx = MTLSContext(cfg)
    assert ctx.verify_client_cert("") is True
    print("PASS test_mtls_verify_client_cert_disabled")


def test_oauth2_token_no_refresh():
    """OAuth2Token refresh_token 可以为 None。"""
    t = OAuth2Token(
        access_token="acc",
        token_type="Bearer",
        expires_in=300,
        refresh_token=None,
        scope="openid",
    )
    assert t.refresh_token is None
    print("PASS test_oauth2_token_no_refresh")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_oauth2_config_frozen,
        test_oauth2_token_fields,
        test_oauth2_token_expiry,
        test_oauth2_userinfo_defaults,
        test_oauth2_userinfo_full,
        test_oauth2_provider_authorization_url,
        test_oauth2_provider_authorization_url_custom_scopes,
        test_oauth2_singleton,
        test_mtls_config_defaults,
        test_mtls_config_custom,
        test_mtls_context_ssl_context_no_certs,
        test_mtls_context_extract_identity_dict,
        test_mtls_context_extract_identity_none,
        test_mtls_singleton,
        test_oauth2_parse_userinfo_with_roles,
        test_oauth2_parse_userinfo_string_roles,
        test_mtls_verify_client_cert_no_whitelist,
        test_mtls_verify_client_cert_disabled,
        test_oauth2_token_no_refresh,
    ]

    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
