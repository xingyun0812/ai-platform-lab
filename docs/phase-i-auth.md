# Phase I — OAuth2 / mTLS (Issue #44)

## 设计概述

本模块在 ai-platform-lab 的现有 JWT HS256 鉴权基础上，以**完全向后兼容、opt-in**的方式引入：

1. **OAuth2**（Authorization Code Flow + Client Credentials Flow）
2. **mTLS**（Mutual TLS 客户端证书验证）

两者均通过环境变量开关控制（默认关闭），关闭时现有 JWT HS256 鉴权逻辑完全不受影响。

---

## 架构决策

### 1. 向后兼容策略
- `OAUTH2_ENABLED` 默认 `false` → 仅启用时 `OAuth2Middleware` 才激活
- `MTLS_ENABLED` 默认 `false` → 仅启用时 `mTLSAuthDependency` 才校验
- 不修改 `packages/auth/jwt_hs256.py` 和 `packages/auth/rbac.py`
- JWT Fallback (`OAUTH2_JWT_FALLBACK=true`)：OAuth2 验证失败时自动回退到 JWT HS256

### 2. OAuth2 流程
```
Authorization Code Flow:
  Browser → GET /internal/auth/oauth2/authorize
          → 302 Redirect → IdP /authorize
          → IdP → POST /internal/auth/oauth2/callback (code)
          → POST token_endpoint → access_token + refresh_token

Client Credentials Flow:
  Service → OAuth2Provider.client_credentials()
          → POST token_endpoint → access_token
```

### 3. mTLS 握手
```
Client ──(TLS Client Hello)──► Server
Server ──(Certificate Request)► Client  [client_cert_required=true]
Client ──(Client Certificate)──► Server
Server verifies cert via CA bundle → extract CN as tenant_id
```

### 4. 中间件顺序
```
Request
  └─► OAuth2Middleware (if oauth2_enabled)
        ├─ verify Bearer token via OAuth2Provider.verify_token()
        ├─ fallback to JWT HS256 if oauth2_jwt_fallback=true
        └─ inject request.state.oauth2_user
  └─► mTLSAuthDependency (FastAPI Depends, if mtls_enabled)
        ├─ extract peer cert from ssl scope
        └─ return MTLSIdentity(client_id, cert_present, verified)
  └─► Route Handler
```

---

## 数据模型

### OAuth2Config
| 字段 | 类型 | 描述 |
|------|------|------|
| `client_id` | `str` | OAuth2 client ID |
| `client_secret` | `str` | OAuth2 client secret |
| `authorization_endpoint` | `str` | IdP 授权端点 URL |
| `token_endpoint` | `str` | Token 端点 URL |
| `userinfo_endpoint` | `str` | Userinfo 端点 URL |
| `redirect_uri` | `str` | 回调 URI |
| `scopes` | `list[str]` | 默认 scope 列表 |
| `issuer` | `str` | IdP issuer URL（用于验证） |

### OAuth2Token
| 字段 | 类型 | 描述 |
|------|------|------|
| `access_token` | `str` | Access token |
| `token_type` | `str` | 通常为 "Bearer" |
| `expires_in` | `int` | 有效期（秒） |
| `refresh_token` | `str \| None` | 刷新 token（可选） |
| `scope` | `str` | 授权的 scope |
| `obtained_at` | `float` | 获取时间戳（`time.time()`） |

### OAuth2UserInfo
| 字段 | 类型 | 描述 |
|------|------|------|
| `sub` | `str` | 用户唯一标识符 |
| `email` | `str \| None` | 邮箱地址 |
| `name` | `str \| None` | 用户姓名 |
| `roles` | `list[str]` | 角色列表（从 roles/groups claim 提取） |
| `metadata` | `dict` | 其余 claims |

### MTLSConfig
| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `enabled` | `bool` | `False` | 是否启用 mTLS |
| `ca_cert_path` | `str` | `""` | CA 证书文件路径 |
| `server_cert_path` | `str` | `""` | 服务器证书路径 |
| `server_key_path` | `str` | `""` | 服务器私钥路径 |
| `client_cert_required` | `bool` | `True` | 是否强制要求客户端证书 |
| `verify_client` | `bool` | `True` | 是否验证客户端证书 |
| `allowed_fingerprints` | `list[str]` | `[]` | 允许的 SHA-256 指纹白名单 |

---

## REST API

前缀：`/internal/auth`

| Method | Path | 描述 | 认证 |
|--------|------|------|------|
| `GET` | `/oauth2/authorize` | 重定向到 IdP 授权页 | 无 |
| `POST` | `/oauth2/callback` | 授权码换 token | 无 |
| `POST` | `/oauth2/refresh` | 刷新 access token | 无 |
| `GET` | `/oauth2/userinfo` | 查询当前用户信息 | Bearer token |
| `GET` | `/mtls/status` | mTLS 配置状态 | X-Tenant-Id + Bearer |
| `GET` | `/config` | 查看所有鉴权方式（admin） | X-Tenant-Id + Bearer（platform_admin） |

### 示例

**GET /internal/auth/oauth2/authorize**
```
HTTP/1.1 302 Found
Location: https://idp.example.com/authorize?response_type=code&client_id=...&state=...
```

**POST /internal/auth/oauth2/callback**
```json
Request:  { "code": "SplxlOBeZQQYbYS6WxSbIA", "state": "xyz" }
Response: { "access_token": "...", "token_type": "Bearer", "expires_in": 3600, ... }
```

**GET /internal/auth/config** (platform_admin only)
```json
{
  "jwt_hs256": { "enabled": true, "description": "HMAC-SHA256 JWT（默认鉴权方式）" },
  "oauth2": { "enabled": false, "client_id": null, "issuer": null, "jwt_fallback": true },
  "mtls": { "enabled": false, "client_cert_required": true }
}
```

---

## 配置（Settings 字段）

> **注意**：以下字段需手动添加到 `apps/gateway/settings.py`

```python
# OAuth2
oauth2_enabled: bool = Field(default=False, validation_alias="OAUTH2_ENABLED",
    description="启用 OAuth2 鉴权（替换 JWT HS256）")
oauth2_client_id: str | None = Field(default=None, validation_alias="OAUTH2_CLIENT_ID")
oauth2_client_secret: str | None = Field(default=None, validation_alias="OAUTH2_CLIENT_SECRET")
oauth2_authorization_endpoint: str = Field(default="", validation_alias="OAUTH2_AUTHORIZATION_ENDPOINT")
oauth2_token_endpoint: str = Field(default="", validation_alias="OAUTH2_TOKEN_ENDPOINT")
oauth2_userinfo_endpoint: str = Field(default="", validation_alias="OAUTH2_USERINFO_ENDPOINT")
oauth2_redirect_uri: str = Field(
    default="http://127.0.0.1:8000/internal/auth/oauth2/callback",
    validation_alias="OAUTH2_REDIRECT_URI",
)
oauth2_scopes: str = Field(default="openid profile email", validation_alias="OAUTH2_SCOPES")
oauth2_issuer: str | None = Field(default=None, validation_alias="OAUTH2_ISSUER")
oauth2_jwt_fallback: bool = Field(default=True, validation_alias="OAUTH2_JWT_FALLBACK",
    description="OAuth2 失败时回退 JWT")

# mTLS
mtls_enabled: bool = Field(default=False, validation_alias="MTLS_ENABLED",
    description="启用 mTLS 客户端证书校验")
mtls_ca_cert_path: str | None = Field(default=None, validation_alias="MTLS_CA_CERT_PATH")
mtls_server_cert_path: str | None = Field(default=None, validation_alias="MTLS_SERVER_CERT_PATH")
mtls_server_key_path: str | None = Field(default=None, validation_alias="MTLS_SERVER_KEY_PATH")
mtls_client_cert_required: bool = Field(default=True, validation_alias="MTLS_CLIENT_CERT_REQUIRED")
```

---

## main.py 集成（DO NOT EDIT — 仅说明）

在 `apps/gateway/main.py` 中添加：

```python
from apps.gateway.auth_routes import router as auth_router

# OAuth2 初始化（仅在 OAUTH2_ENABLED=true 时）
if settings.oauth2_enabled:
    from packages.auth.oauth2 import init_oauth2_provider, OAuth2Config
    init_oauth2_provider(OAuth2Config(
        client_id=settings.oauth2_client_id or "",
        client_secret=settings.oauth2_client_secret or "",
        authorization_endpoint=settings.oauth2_authorization_endpoint,
        token_endpoint=settings.oauth2_token_endpoint,
        userinfo_endpoint=settings.oauth2_userinfo_endpoint,
        redirect_uri=settings.oauth2_redirect_uri,
        scopes=settings.oauth2_scopes.split(),
        issuer=settings.oauth2_issuer or "",
    ))
    from packages.auth.middleware import OAuth2Middleware
    app.add_middleware(
        OAuth2Middleware,
        oauth2_enabled=True,
        jwt_fallback=settings.oauth2_jwt_fallback,
        jwt_secret=settings.auth_jwt_secret,
    )

# mTLS 初始化（仅在 MTLS_ENABLED=true 时）
if settings.mtls_enabled:
    from packages.auth.mtls import init_mtls_context, MTLSConfig
    init_mtls_context(MTLSConfig(
        enabled=True,
        ca_cert_path=settings.mtls_ca_cert_path or "",
        server_cert_path=settings.mtls_server_cert_path or "",
        server_key_path=settings.mtls_server_key_path or "",
        client_cert_required=settings.mtls_client_cert_required,
    ))

app.include_router(auth_router)
```

---

## .env.example 条目

```dotenv
# ── OAuth2 ──────────────────────────────────────────────────────
OAUTH2_ENABLED=false
# OAUTH2_CLIENT_ID=your-client-id
# OAUTH2_CLIENT_SECRET=your-client-secret
# OAUTH2_AUTHORIZATION_ENDPOINT=https://idp.example.com/authorize
# OAUTH2_TOKEN_ENDPOINT=https://idp.example.com/token
# OAUTH2_USERINFO_ENDPOINT=https://idp.example.com/userinfo
# OAUTH2_REDIRECT_URI=http://127.0.0.1:8000/internal/auth/oauth2/callback
# OAUTH2_SCOPES=openid profile email
# OAUTH2_ISSUER=https://idp.example.com
# OAUTH2_JWT_FALLBACK=true

# ── mTLS ────────────────────────────────────────────────────────
MTLS_ENABLED=false
# MTLS_CA_CERT_PATH=/etc/ssl/certs/ca.pem
# MTLS_SERVER_CERT_PATH=/etc/ssl/certs/server.crt
# MTLS_SERVER_KEY_PATH=/etc/ssl/private/server.key
# MTLS_CLIENT_CERT_REQUIRED=true
```

---

## README 片段

```markdown
### OAuth2 / mTLS (Issue #44)

Production-grade auth extensions — **opt-in, disabled by default**.

| Feature | Env Var | Default |
|---------|---------|---------|
| OAuth2 Authorization Code | `OAUTH2_ENABLED=true` | `false` |
| OAuth2 Client Credentials | `OAUTH2_ENABLED=true` | `false` |
| JWT Fallback | `OAUTH2_JWT_FALLBACK=true` | `true` |
| mTLS Client Cert | `MTLS_ENABLED=true` | `false` |

See `docs/phase-i-auth.md` for full configuration guide.
```

---

## Roadmap 更新

```markdown
| Phase I | #44 | OAuth2 / mTLS | `packages/auth/oauth2.py`, `packages/auth/mtls.py`, `packages/auth/middleware.py` | done |
```

---

## 测试

```
python3 tests/test_auth_oauth2_mtls.py
```

共 19 个测试用例，涵盖：
- `OAuth2Config` frozen dataclass
- `OAuth2Token` 字段与过期逻辑
- `OAuth2UserInfo` 默认值与完整字段
- `OAuth2Provider.get_authorization_url()` URL 构造
- 自定义 scopes 覆盖
- 全局单例（init / get / reset）
- `MTLSConfig` 默认值与自定义
- `MTLSContext.get_ssl_context()` 无证书路径
- `extract_client_identity()` dict 格式 + None
- mTLS 单例
- `_parse_userinfo()` roles list + string
- `verify_client_cert()` 白名单为空 + verify=false
- `OAuth2Token` refresh_token=None

---

## 代码导航

| 文件 | 职责 |
|------|------|
| `packages/auth/oauth2.py` | OAuth2 数据模型 + Provider + 全局单例 |
| `packages/auth/mtls.py` | MTLSConfig + MTLSContext + SSLContext 构建 + 全局单例 |
| `packages/auth/middleware.py` | OAuth2Middleware (BaseHTTPMiddleware) + mTLSAuthDependency |
| `packages/auth/__init__.py` | 统一导出 |
| `apps/gateway/auth_routes.py` | REST API (/internal/auth 前缀) |
| `tests/test_auth_oauth2_mtls.py` | 单元测试（19 cases） |

---

## 已知限制

1. **无 JWKS 缓存** — `verify_token()` 每次调用 userinfo endpoint，未缓存 JWKS 公钥；高并发场景需添加 TTL 缓存
2. **无 PKCE 支持** — Authorization Code Flow 未实现 PKCE (`code_challenge`/`code_verifier`)，不适用于公开客户端（SPA）
3. **mTLS 证书轮换需手动重启** — `MTLSContext.get_ssl_context()` 构建时加载证书，不支持运行时热轮换
4. **无 SLO（Single Logout）集成** — 不支持 IdP 推送的 logout 通知，token 过期前无法强制下线
5. **无多 IdP 支持** — 当前单例设计只支持一个 OAuth2 provider；多 IdP 场景需重构为 registry 模式
6. **aiohttp 为可选依赖** — 未安装 `aiohttp` 时调用 token/userinfo endpoint 抛出 `RuntimeError`；生产环境需确保安装
7. **mTLS 指纹验证为桩实现** — `verify_client_cert()` 的指纹匹配逻辑仅做示例，生产需使用 `cryptography` 库完整验证证书链

---

## 面试要点

1. **为什么 OAuth2 + JWT 可以并存？**
   - 两者职责不同：OAuth2 是授权框架，JWT 是令牌编码格式。可用 JWT 作为 OAuth2 的 access token 格式（即 JWT Bearer），也可用 opaque token 配合 userinfo endpoint 验证
   - 本实现通过 `jwt_fallback` 机制实现渐进迁移：先启用 OAuth2，失败时回退 JWT，最终完全切换

2. **Authorization Code Flow vs Client Credentials Flow 的使用场景？**
   - Authorization Code：有用户参与的 Web/移动应用，需要用户同意授权（consent）
   - Client Credentials：无用户参与的机器对机器通信（M2M），如微服务间调用

3. **mTLS 相比单向 TLS 的优势？**
   - 双向认证：服务端验证客户端身份，防止未经授权的客户端连接
   - 适用于内部微服务网格（Service Mesh），客户端 CN 可作为服务身份（SPIFFE/SVID）
   - 比 API Key 更难伪造（私钥不离开客户端）

4. **token 验证性能优化？**
   - 当前：每次调用 userinfo endpoint（网络开销大）
   - 优化方案：缓存验证结果（TTL = expires_in - buffer），或使用 JWKS 本地验证 JWT 签名（无需网络）
   - 进一步：使用 Redis 共享 token 黑名单（支持 revocation）

5. **如何处理 token 刷新竞争条件？**
   - 多个请求并发触发 refresh → 可能发出多个刷新请求，部分 IdP 会使旧 refresh_token 失效
   - 解决方案：使用分布式锁（Redis SETNX + TTL）或 CAS 操作保证只有一个 refresh 请求在途

6. **本模块的向后兼容保证？**
   - `OAUTH2_ENABLED=false`（默认）：`OAuth2Middleware.dispatch()` 直接 `call_next(request)`，零开销
   - 不修改任何现有文件（jwt_hs256.py、rbac.py、main.py、settings.py）
   - 现有 API 测试（如 test_mcp.py）完全不受影响
   - 通过 `getattr(settings, 'oauth2_enabled', False)` 安全读取新字段，settings 未更新时降级返回 503
