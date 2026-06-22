# Phase H #40 — HITL 完整工作流

## 概述

Human-in-the-Loop（HITL）工作流为高风险工具调用提供完整的人工审批流程。
本阶段在原有 `packages/agent/hitl.py` JSON 文件存储 stub 的基础上，
引入完整的审批队列、Webhook 通知和超时处理机制。

构建思路、使用链路与逐文件代码说明见 [phase-h-build-and-code-guide.md](./phase-h-build-and-code-guide.md)。

---

## 设计要点

1. **双存储引擎**：默认 `InMemoryApprovalStore`（无状态/测试），可选 `SqliteApprovalStore`（持久化）
2. **向后兼容**：`packages/agent/hitl.py` 保持原有接口（`ApprovalStatus`、`get_approval`、`create_pending_execution` 等），通过委托新 `packages.hitl` 实现
3. **Webhook HMAC-SHA256**：所有出站通知携带 `X-Hitl-Signature: sha256=<hex>` 签名
4. **指数退避重试**：Webhook 失败后 1s / 2s / 4s 三次重试
5. **超时扫描**：`timeout_expired_requests()` 后台任务定期将过期 pending 标记为 `timeout`
6. **线程安全**：所有存储操作使用 `threading.RLock` 保护
7. **优雅降级**：store 未初始化时返回 503，webhook 失败不抛出异常

---

## 数据模型

### ApprovalRequest

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | str | UUID，唯一标识 |
| `tenant_id` | str | 租户 ID |
| `session_id` | str | 会话 ID |
| `tool_name` | str | 被审批的工具名称 |
| `arguments` | dict | 工具调用参数 |
| `created_at` | float | Unix 时间戳（创建） |
| `expires_at` | float | Unix 时间戳（超时） |
| `status` | str | `pending`/`approved`/`rejected`/`timeout`/`cancelled` |
| `decided_by` | str\|None | 决策人 |
| `decided_at` | float\|None | 决策时间戳 |
| `decision_reason` | str\|None | 决策原因 |
| `webhook_sent` | bool | 是否已发送 Webhook |
| `metadata` | dict | 扩展元数据 |

### ApprovalDecision

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | str | 对应 ApprovalRequest.request_id |
| `status` | str | `approved` / `rejected` / `cancelled` |
| `decided_by` | str | 决策人 |
| `reason` | str\|None | 原因 |
| `decided_at` | float | 决策时间 |

### WebhookConfig

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `url` | str | — | Webhook 目标 URL |
| `headers` | dict | `{}` | 附加请求头 |
| `secret` | str | `""` | HMAC 签名密钥 |
| `enabled` | bool | `True` | 是否启用 |

---

## Webhook 签名方案

```
body = JSON.stringify(payload, ensure_ascii=False).encode("utf-8")
sig  = "sha256=" + HMAC-SHA256(key=secret.encode(), msg=body).hexdigest()
```

请求头：`X-Hitl-Signature: sha256=<64位十六进制>`

接收方验证：
```python
from packages.hitl.webhook import verify_signature
ok = verify_signature(secret, request.body, request.headers["X-Hitl-Signature"])
```

---

## REST API 表

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| `POST` | `/internal/hitl/approvals` | 已认证 | 创建审批请求 |
| `GET` | `/internal/hitl/approvals/{request_id}` | 已认证 | 查询单条审批状态 |
| `GET` | `/internal/hitl/approvals?tenant_id=&status=` | 已认证 | 列出待审批请求 |
| `POST` | `/internal/hitl/approvals/{request_id}/approve` | platform_admin | 批准 |
| `POST` | `/internal/hitl/approvals/{request_id}/reject` | platform_admin | 拒绝 |
| `POST` | `/internal/hitl/approvals/{request_id}/cancel` | platform_admin | 取消 |
| `POST` | `/internal/hitl/webhooks/test` | platform_admin | 测试 Webhook 配置 |

### 请求/响应示例

**POST /internal/hitl/approvals**
```json
{
  "tenant_id": "tenant-a",
  "session_id": "sess-123",
  "tool_name": "delete_database",
  "arguments": {"db_name": "prod"},
  "timeout_seconds": 300,
  "webhook_url": "https://example.com/hooks/hitl",
  "webhook_secret": "my-secret"
}
```

响应 `201`:
```json
{
  "request_id": "uuid-...",
  "status": "pending",
  "expires_at": 1234567890.0,
  ...
}
```

---

## 配置表（需集成到 settings.py）

| 字段名 | 环境变量 | 默认 | 说明 |
|--------|---------|------|------|
| `hitl_enabled` | `HITL_ENABLED` | `true` | 启用 HITL 审批工作流 |
| `hitl_store_database_url` | `HITL_STORE_DATABASE_URL` | `None` | 审批队列存储；`sqlite:///path/db` 或 `None`（内存） |
| `hitl_default_timeout_seconds` | `HITL_DEFAULT_TIMEOUT_SECONDS` | `300` | 审批默认超时（秒） |
| `hitl_webhook_url` | `HITL_WEBHOOK_URL` | `None` | 审批通知 Webhook URL |
| `hitl_webhook_secret` | `HITL_WEBHOOK_SECRET` | `None` | Webhook HMAC 签名密钥 |
| `hitl_expiry_check_interval_seconds` | `HITL_EXPIRY_CHECK_INTERVAL_SECONDS` | `60` | 超期检查后台任务间隔 |

### settings.py 字段声明（供父 Agent 添加）

```python
hitl_enabled: bool = Field(default=True, validation_alias="HITL_ENABLED", description="启用 HITL 审批工作流")
hitl_store_database_url: str | None = Field(default=None, validation_alias="HITL_STORE_DATABASE_URL", description="HITL 审批队列存储；None=内存")
hitl_default_timeout_seconds: int = Field(default=300, validation_alias="HITL_DEFAULT_TIMEOUT_SECONDS", description="审批默认超时")
hitl_webhook_url: str | None = Field(default=None, validation_alias="HITL_WEBHOOK_URL", description="审批通知 webhook URL")
hitl_webhook_secret: str | None = Field(default=None, validation_alias="HITL_WEBHOOK_SECRET", description="webhook HMAC 签名密钥")
hitl_expiry_check_interval_seconds: int = Field(default=60, validation_alias="HITL_EXPIRY_CHECK_INTERVAL_SECONDS", description="过期检查间隔")
```

---

## main.py 集成（供父 Agent 添加）

```python
from apps.gateway.hitl_routes import router as hitl_router

# 在 app 创建后、lifespan 中：
if settings.hitl_enabled:
    from packages.hitl import init_approval_store
    init_approval_store(database_url=settings.hitl_store_database_url)

app.include_router(hitl_router)
```

---

## .env.example 新增项（供父 Agent 添加）

```dotenv
# HITL 审批工作流
HITL_ENABLED=true
HITL_STORE_DATABASE_URL=          # sqlite:///data/hitl.db 或空（内存模式）
HITL_DEFAULT_TIMEOUT_SECONDS=300
HITL_WEBHOOK_URL=                 # https://example.com/hooks/hitl
HITL_WEBHOOK_SECRET=              # 随机字符串，用于 HMAC 签名
HITL_EXPIRY_CHECK_INTERVAL_SECONDS=60
```

---

## README 新增章节（供父 Agent 添加）

### HITL 审批工作流

AI Platform Lab 内置 Human-in-the-Loop（HITL）机制：高风险工具在执行前需要管理员审批。

**工作流程：**
1. agent runner 识别高风险工具调用，通过 `create_pending_execution()` 创建审批请求
2. 可选：通过 Webhook 通知外部系统（HMAC-SHA256 签名保护）
3. 管理员通过 `POST /internal/hitl/approvals/{id}/approve` 批准
4. agent 通过 `resume_approved_tool()` 恢复执行
5. 未在超时时间内审批的请求自动标记为 `timeout`

**启用方式：** 设置 `HITL_ENABLED=true`（默认已启用）

---

## 测试说明

```bash
python3 tests/test_hitl.py
```

测试覆盖：
- `InMemoryApprovalStore` CRUD
- list_pending 租户隔离
- approve / reject / cancel
- 重复决策防护
- 超时扫描（expire_stale）
- Webhook HMAC 签名计算与验证
- `SqliteApprovalStore` create + approve
- 全局单例生命周期
- service 层 request_approval + check_approval
- service 层 approve + reject

---

## 代码导航

| 文件 | 职责 |
|------|------|
| `packages/hitl/store.py` | 数据模型 + 存储引擎（内存/SQLite）+ 全局单例 |
| `packages/hitl/webhook.py` | Webhook 发送（aiohttp + HMAC + 指数退避） |
| `packages/hitl/service.py` | 业务逻辑层（request/approve/reject/timeout） |
| `packages/hitl/__init__.py` | 公共导出 + 向后兼容 get_approval |
| `packages/agent/hitl.py` | 向后兼容 shim，委托给 packages.hitl |
| `apps/gateway/hitl_routes.py` | FastAPI REST 路由（/internal/hitl） |
| `tests/test_hitl.py` | 14 个独立单元测试 |

---

## 已知限制

1. **内存存储无持久化**：服务重启丢失所有审批记录，生产环境必须配置 `HITL_STORE_DATABASE_URL`
2. **SQLite 并发限制**：SQLite 不支持高并发写入，多实例部署时应使用 PostgreSQL（需扩展实现 `PostgresApprovalStore`）
3. **get_approval 同步包装**：`packages/hitl/__init__.py` 中的同步 `get_approval` 在已有事件循环中返回 `None`（避免死锁），可能影响嵌套调用
4. **Webhook 无消息队列**：失败重试后直接放弃，不保证至少一次送达
5. **无审批通知回调**：被审批人目前只能通过轮询 GET 端点获知结果，缺乏主动推送
6. **超时扫描需手动触发**：`timeout_expired_requests()` 需要外部调度器定期调用（如 APScheduler / Celery Beat），框架未自动启动后台任务

---

## 面试要点

1. **为什么用 HMAC-SHA256 而不是 Bearer Token 验证 Webhook？**
   Webhook 是服务器推送，接收方无法主动验证发起方身份。HMAC 签名让接收方用共享密钥本地验证消息完整性，防止伪造和篡改，与 GitHub/Stripe 的 Webhook 签名方案一致。

2. **InMemoryStore 如何实现线程安全？**
   使用 `threading.RLock`（可重入锁）保护所有读写操作。选择 RLock 而非 Lock 是因为同一线程可能在 `expire_stale` 等场景下重入，避免死锁。

3. **为什么 `packages/agent/hitl.py` 保持 JSON 文件 fallback 而不完全委托？**
   渐进式迁移策略：`HITL_ENABLED=false` 或新 store 未初始化时优雅降级，不影响现有运行环境。符合"不破坏现有接口"的向后兼容原则。

4. **`SqliteApprovalStore` 为何不用 async SQLite 库（如 aiosqlite）？**
   标准库 `sqlite3` 加 `threading.RLock` 在低并发场景下更简单可靠。真正需要高并发时应替换为 PostgreSQL + asyncpg，而不是强行异步化 SQLite。

5. **超时设计：为什么用 expires_at（绝对时间戳）而不是 TTL？**
   绝对时间戳更易于跨进程/跨重启比较（SQLite 持久化场景），避免相对 TTL 在序列化后失去意义的问题。`expire_stale()` 只需一条 `WHERE expires_at < now()` 查询。

6. **Webhook 指数退避为什么选 1s/2s/4s 而不更长？**
   HITL 审批是时间敏感场景，用户等待审批通知。退避上限 4s 在 3 次重试总耗时约 7s，兼顾可靠性和响应性。生产环境可配置为更长间隔或改为异步队列重试。
