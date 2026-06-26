# Phase B1：Token 计量与租户预算

在 [Phase A](./phase-a-internal-beta.md) 之上，用 **Postgres** 记录 LLM token 用量，并支持租户 **日/月预算** 拦截。路线图见 [roadmap.md](./roadmap.md)。

关联 Issues：[#5](https://github.com/xingyun0812/ai-platform-lab/issues/5) · [#6](https://github.com/xingyun0812/ai-platform-lab/issues/6)

构建思路、使用链路与逐文件代码说明见 [phase-b-build-and-code-guide.md](./phase-b-build-and-code-guide.md)。

---

## 架构

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e'}}}%%
sequenceDiagram
  participant Client
  participant Gateway
  participant PG as Postgres usage_records
  participant LLM

  Client->>Gateway: POST /v1/chat/completions
  Gateway->>Gateway: check_token_budget
  Gateway->>LLM: 转发
  LLM-->>Gateway: 200 + usage
  Gateway->>PG: INSERT usage_records
  Gateway-->>Client: 200 + _platform.usage
```

---

## 环境

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | Postgres 连接串；未配置或不可达时跳过计费 |
| `token_budget_daily` | `config/tenants.yaml`，-1 不限 |
| `token_budget_monthly` | 自然月 UTC 汇总 |

Compose 默认：

```text
DATABASE_URL=postgresql://aiplatform:aiplatform@postgres:5432/ai_platform_lab
```

---

## 演示

### 查询用量（admin）

```bash
curl -s "http://127.0.0.1:8000/internal/billing/usage?hours=24" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .
```

### 导出 CSV（仅 admin）

```bash
curl -s "http://127.0.0.1:8000/internal/billing/export?hours=24&format=csv" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" -o usage.csv
```

### Console 租户页「本月使用」

`GET /internal/tenants` 对每个租户调用 `get_budget_snapshot()`，与上表 API 同源。详见 [console-tenant-billing.md](./console-tenant-billing.md)。

```bash
curl -s http://127.0.0.1:8000/internal/tenants \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq '.[0] | {tenant_id, tokens_used_this_month, billing_available}'
```

### 预算拦截

`demo-b` 配置了 `token_budget_daily: 500`。在 Postgres 中累计超过后：

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "X-Tenant-Id: demo-b" \
  -H "Authorization: Bearer sk-tenant-demo-b-change-me" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}]}'
# → 429 BUDGET_EXCEEDED（需已配置 LLM_API_KEY 且用量超限）
```

成功响应可选带 `_platform.usage`：

```json
{
  "choices": [...],
  "_platform": {
    "usage": {
      "input_tokens": 12,
      "output_tokens": 8,
      "total_tokens": 20,
      "budget_remaining_daily": 480
    }
  }
}
```

---

## 代码导读

| 模块 | 路径 |
|------|------|
| 解析 usage | `packages/billing/usage.py` |
| Postgres 落库 | `packages/billing/store.py` |
| 预算检查 | `packages/billing/budget.py` |
| 记录钩子 | `packages/billing/recorder.py` |
| 查询 API | `apps/gateway/billing_routes.py` |

---

## 验收

- [ ] `docker compose up` 含 postgres healthy
- [ ] chat 成功后 `usage_records` 有行
- [ ] `GET /internal/billing/usage` 返回聚合
- [ ] `demo-b` 超限返回 `429 BUDGET_EXCEEDED`
- [ ] 无 `DATABASE_URL` 时主路径不受影响

---

## 已知限制（B1）

- 仅记录 **chat/completions** 类响应的 usage；embedding 未单独计量
- 预算为 **硬拦截**（已用 ≥ 预算），非预估本次请求 token
- 无单价账单、无发票（Phase B 后续 / Phase C）
