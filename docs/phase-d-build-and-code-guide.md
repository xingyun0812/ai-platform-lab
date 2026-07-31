# Phase D 构建思路与代码导读：运维与治理

> 操作手册：[phase-d-ops.md](./phase-d-ops.md) · 远期：[phase-d-future-evolution.md](./phase-d-future-evolution.md) · 前置：[Phase C](./phase-c-build-and-code-guide.md)

---

## 目录

1. [构建思路](#1-构建思路)
2. [使用链路](#2-使用链路)
3. [代码导读（按文件）](#3-代码导读按文件)
4. [设计决策](#4-设计决策)
5. [操作命令](#5-操作命令)
6. [自测用例](#6-自测用例)

---

## 1. 构建思路

| 波次 | 能力 | 核心路径 |
|------|------|----------|
| D1 | 熔断 + Grafana | `packages/router/circuit_breaker.py`, `config/grafana/` |
| D2 | JWT + RBAC + Postgres 审计 | `packages/auth/jwt_hs256.py`, `packages/audit/postgres_store.py` |
| D3 | 控制台 MVP | `apps/console/index.html`, `/console/` |
| D4 | Redis Session + 金丝雀守卫 | `packages/agent/session_redis.py`, `packages/rag/canary_guard.py` |
| D5 | 成本估算 | `packages/billing/cost.py`, `/internal/billing/invoice` |

**原则**：观测与治理不侵入业务主路径——中间件/守卫层挂载，失败可降级。

### D1 熔断（Circuit Breaker）

**动机**：上游模型 API 不稳定时快速失败，防止网关线程堆积，给上游恢复窗口。

**三态状态机**：

| 状态 | 含义 | 触发 |
|------|------|------|
| `closed` | 正常转发 | 初始 / 连续成功 |
| `open` | 直接返回 503 | 连续 `failure_threshold`（默认 5）次失败 |
| `half_open` | 探活窗口 | `recovery_seconds`（默认 30s）后自动进入 |

**线程安全**：`threading.Lock` 保护 `_failures` / `_opened_at` / `_half_open`，进程内单例即可，无需 Redis。

**集成方式**：`model_router.py` 在调用 LLM 前 `allow(model)` → 成功 `record_success` / 失败 `record_failure`。

**设计取舍**：
- 按 model name 分 key，不影响健康模型
- 不持久化状态：gateway 重启后熔断重置（比持久化更安全——故障可能已恢复）

### D2 JWT + RBAC + 审计双写

**JWT HS256**（`jwt_hs256.py`）：

纯标准库实现，无外部依赖：
```
token → base64url decode header → 校验 alg=="HS256"
     → hmac.compare_digest 验签 → 返回 payload dict
```

**刻意省略**：`exp` 校验——保持最小解析，过期由 API 网关或调用方负责。

**RBAC**（`rbac.py`）：

```
viewer(0) < developer(1) < tenant_admin(2) < platform_admin(3)
```

- `role_at_least()` 按元组 index 比较
- 跨租户访问 / PATCH 配额 / 工具审批 → 需 `platform_admin`
- `can_view_tenant_profile()`：同租户直接放行，跨租户需 `platform_admin`

**审计双写**（`postgres_store.py`）：

- SQLite 始终写入（Phase A 基线保障）
- `DATABASE_URL` 存在时同步写 Postgres `audit_events` 表
- 表结构：`id (BIGSERIAL), created_at, tenant_id, actor_role, method, path, status_code, latency_ms, trace_id, model, error_code`
- 索引 `(created_at DESC)`，初始化 `__init__` 时 `CREATE TABLE IF NOT EXISTS`

### D3 控制台 MVP

纯静态 React SPA，Gateway 在 `/console/` 路由挂载。后端 JSON API 定义在 `console_routes.py`：

| 端点 | 用途 |
|------|------|
| `POST /internal/auth/token` | 登录 |
| `GET /internal/tenants` | 租户列表 |
| `GET /internal/tenants/{id}` | 租户画像 |
| `GET /internal/metrics` | 仪表盘 |
| `GET /internal/regions` | 区域矩阵 |
| `GET /internal/usage` | 用量排行 |
| `GET /internal/settings` | 配置快照 |
| `GET /internal/tools` | 工具市场 |
| `POST /internal/tools/approve` | 审批工具 |
| `GET/POST /internal/knowledge-bases` | 知识库 CRUD |
| `GET /internal/knowledge-bases/{id}/documents` | 文档列表 |
| `POST /internal/rag/query` | RAG 调试查询 |

### D4 Redis Session + 金丝雀守卫 + MCP stub

**Redis Session**（`session_redis.py`）：

- Key 格式：`ai_platform:session:{tenant_id}:{session_id}`，TTL 86400s
- 提供 `get/save_session_state()` 和 `get/save_messages()`
- `REDIS_URL` 未配置 → 自动降级为 `InMemorySessionStore`
- 多 gateway 副本共享同一 Redis 即可保持 session 一致

**金丝雀守卫**（`canary_guard.py` ~223 行）：

```
check_canary_guard(kb_id, min_pass_rate=0.85) → CanaryCheckResult
```

流程：
1. 扫描 `eval/runs/*.json` 找到最新 eval report
2. pass_rate < 阈值 → 写入 `data/canary_guard.json`（`canary_percent=0`）
3. 发送 webhook（`CANARY_GUARD_WEBHOOK_URL` + `CANARY_GUARD_WEBHOOK_SECRET`）
4. 记录 metric `canary_auto_rollback`
5. 返回 action: `"noop"` 或 `"rollback"`

支持 `dry_run` 模式预览不执行；提供 CLI 命令 `check` 和 `status`。

**MCP stub**（`config/mcp_tools.json`）：示范工具 `mcp_echo`，展示外部工具集成框架。

### D5 成本估算（`cost.py`）

```
estimate_cost_usd(model, input_tokens, output_tokens) → float
```

- 单价来自 `get_provider_matrix()` → `config/providers.yaml` 每个 offering
- 计算：`(input_tokens / 1000 * input_price_per_1k) + (output_tokens / 1000 * output_price_per_1k)`
- 匹配不到 model 返回 0.0
- 端点 `GET /internal/billing/invoice?month=2026-05` 聚合按月估算

**注意**：价格为示意单价，非正式计费。

---

## 2. 使用链路

### 2.1 JWT 鉴权 + 审计双写

```mermaid
sequenceDiagram
  participant C as Client
  participant MW as TraceIdMiddleware
  participant T as resolve_tenant
  participant App as 业务路由
  participant Audit as SQLite+Postgres

  C->>MW: Bearer JWT
  MW->>T: 解析 tenant_id role
  T->>App: 通过
  App-->>C: 响应
  MW->>Audit: 双写 audit_events
```

### 2.2 熔断打开

```mermaid
flowchart TD
  F["上游连续失败"] --> CB["circuit_breaker 计数"]
  CB --> O{"达阈值?"}
  O -->|是| R["503 CIRCUIT_OPEN"]
  O -->|否| U["继续转发"]
```

---

## 3. 代码导读（按文件）

### `packages/router/circuit_breaker.py`（D1 熔断）

**67 行，纯标准库，无外部依赖。**

核心数据结构：

```python
@dataclass
class CircuitBreaker:
    failure_threshold: int = 5      # 连续失败阈值
    recovery_seconds: float = 30.0  # 打开后等待秒数

    # 线程安全：所有读写锁保护
    _failures: dict[str, int]      # key → 连续失败计数
    _opened_at: dict[str, float]   # key → 打开时 monotonic 时间戳
    _half_open: set[str]           # 探活中
```

关键方法时序：

| 调用点 | 动作 | 状态变迁 |
|--------|------|----------|
| `allow(key)` → `(ok, state)` | 查状态 | open → 拒绝；其他 → 放行 |
| `record_success(key)` | 清零 | 任何状态 → closed |
| `record_failure(key)` | 计数 | 达阈值 → open；half_open 下失败 → 回 open |

**单例**：`get_circuit_breaker()` 返回进程内单例，gateway 各路由共享同一熔断器。

### `packages/auth/jwt_hs256.py`（D2 JWT）

**33 行，最小 HS256 实现。**

```python
def decode_hs256(token: str, secret: str) -> dict[str, Any] | None:
    parts = token.split(".")         # header.payload.signature
    header = json.loads(_b64url_decode(parts[0]))
    if header.get("alg") != "HS256": # 只接受 HS256
        return None
    sig = _b64url_decode(parts[2])
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):  # 常量时间比较
        return None
    return json.loads(_b64url_decode(parts[1]))  # payload
```

**为什么不用 PyJWT 库？** 仅需 HS256 验签，标准库 30 行解决，减少一个依赖。

### `packages/auth/rbac.py`（D2 RBAC）

**25 行，元组层级比较。**

```python
ROLE_HIERARCHY = ("viewer", "developer", "tenant_admin", "platform_admin")

def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_HIERARCHY.index(role) >= ROLE_HIERARCHY.index(minimum)
```

三条策略函数封装了所有鉴权判断点：`can_patch_tenant_limits`、`can_approve_tools`、`can_view_tenant_profile`。

### `packages/audit/postgres_store.py`（D2 审计双写）

自动建表，`__init__` 中执行：

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    tenant_id TEXT, actor_role TEXT,
    method TEXT, path TEXT,
    status_code INT, latency_ms INT,
    trace_id TEXT, model TEXT, error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events (created_at DESC);
```

写入方法 `record_event(**fields)` 接收所有字段为关键字参数，与 `TraceIdMiddleware` 的审计钩子对接。

### `packages/agent/session_redis.py`（D4 Redis Session）

双实现：

| 条件 | 实现 |
|------|------|
| `REDIS_URL` 已配置 | `RedisSessionStore` — key `ai_platform:session:{tenant_id}:{session_id}`，TTL 86400s |
| `REDIS_URL` 未配置 | `InMemorySessionStore` — `dict[str, dict]` 进程内存 |

接口：`save_session_state(session_id, tenant_id, state)` / `get_session_state(session_id, tenant_id)` 及其 Message 变体。

### `packages/rag/canary_guard.py`（D4 金丝雀守卫）

**~223 行**，含 CLI 入口。

核心流程：

```python
@dataclass
class CanaryCheckResult:
    kb_id: str
    pass_rate: float
    min_pass_rate: float
    action: str           # "noop" | "rollback"
    detail: str

def check_canary_guard(kb_id, min_pass_rate=0.85, eval_path=None, dry_run=False):
    report = _latest_eval_report(eval_path)  # 扫描 eval/runs/*.json
    if report.pass_rate < min_pass_rate:
        if not dry_run:
            _write_canary_file(kb_id, 0)     # data/canary_guard.json
            _send_webhook(...)               # CANARY_GUARD_WEBHOOK_URL
            _record_metric(...)              # canary_auto_rollback
        return CanaryCheckResult(action="rollback", ...)
    return CanaryCheckResult(action="noop", ...)
```

### `packages/billing/cost.py`（D5 成本估算）

**17 行**，纯函数：

```python
def estimate_cost_usd(*, model, input_tokens, output_tokens) -> float:
    matrix = get_provider_matrix()          # config/providers.yaml
    for offering in matrix.offerings:
        if offering.model == model:
            return (input_tokens / 1000) * offering.input_price_per_1k \
                 + (output_tokens / 1000) * offering.output_price_per_1k
    return 0.0
```

### 配置与运维文件

| 文件 | 内容 |
|------|------|
| `config/prometheus/alerts.yml` | 告警规则（Gateway 高延迟 / 熔断频繁等） |
| `config/grafana/dashboards/gateway-overview.json` | Grafana 面板（QPS、P50/P95 延迟、熔断次数） |
| `config/mcp_tools.json` | MCP 工具 stub 定义 |
| `data/canary_guard.json` | 金丝雀守卫运行时状态（自动写入） |

---

## 4. 设计决策

| 决策 | 选型 | 理由 |
|------|------|------|
| 熔断状态不持久化 | 进程内存 | Gateway 重启 = 故障可能已恢复，重置比持久更安全 |
| JWT 无 exp 校验 | 纯标准库最小实现 | 调用方 / 上层网关负责过期，保持代码可审计 |
| 审计双写（非事务） | SQLite + Postgres | SQLite 兜底保证不丢，Postgres 可查；不强求双写原子性 |
| Redis Session TTL 86400s | 24 小时 | 对话 session 典型生命周期；配合滑续无需更长 |
| 金丝雀阈值硬编码默认 0.85 | 环境变量可覆盖 | 85% 是评估 pipeline 质量最低接受线 |
| 单价来自 providers.yaml | 配置驱动 | 不改代码即可调价；非正式计费不设计数据库表 |

---

## 5. 操作命令

```bash
# D1 多副本 Gateway（共享 Redis 配额）
docker compose up -d --scale gateway=2

# D1 可观测 + Grafana
docker compose --profile observability up -d
# 浏览器打开 http://127.0.0.1:3000  admin/admin

# D2 启用 JWT
export AUTH_JWT_ENABLED=true
export AUTH_JWT_SECRET=your-dev-secret

# D3 控制台
open http://127.0.0.1:8000/console/

# D4 自动回滚检查
python -c "from packages.rag.canary_guard import check_canary_guard; print(check_canary_guard('lab-demo', dry_run=True))"

# D5 成本估算
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  "http://127.0.0.1:8000/internal/billing/invoice?month=2026-05" | jq .

# 验收
python eval/acceptance_smoke.py
```

---

## 6. 自测用例

| # | 输入 | 预期 |
|---|------|------|
| 1 | AUTH_JWT_ENABLED + 合法 JWT | 200 |
| 2 | JWT 过期 | 401 |
| 3 | 上游连续 5xx | 503 CIRCUIT_OPEN |
| 4 | scale gateway=2 + Redis | 配额跨实例一致 |
| 5 | GET /console/ | 静态控制台 |
| 6 | agent run 带 session_id | Redis 存历史 |
| 7 | eval pass_rate 低于阈值 | 金丝雀 traffic=0 |
| 8 | GET /internal/billing/invoice | 月估算 |
| 9 | GET /metrics + Grafana | 面板有数据 |
| 10 | audit Postgres 开启 | 双写可查 |
