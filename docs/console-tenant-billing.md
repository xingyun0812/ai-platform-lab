# Console — 租户本月用量（Billing 对接）

> **页面**：`http://127.0.0.1:8000/console/tenants` → **本月使用** 列  
> **数据源**：Postgres `usage_records`（Phase B1），与 `GET /internal/billing/usage` 同源

---

## 1. 数据流

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'lineColor': '#8b949e'}}}%%
flowchart LR
    LLM["Chat / Agent / RAG"] --> REC["billing recorder"]
    REC --> PG[("Postgres usage_records")]
    PG --> SNAP["get_budget_snapshot"]
    SNAP --> API["GET /internal/tenants"]
    API --> UI["Console 租户管理"]
```

| 字段 | 说明 |
|------|------|
| `tokens_used_this_month` | 自然月 UTC 起累计 token（`sum_tokens`） |
| `tokens_used_today` | 当日 UTC 累计（API 返回，Console 暂未展示） |
| `quota_tokens_per_month` | `tenants.yaml` 的 `token_budget_monthly`；`-1` 为不限 |
| `billing_available` | `DATABASE_URL` 可达且 billing store 可用时为 `true` |

---

## 2. 环境配置

```bash
# docker compose up -d postgres 后
DATABASE_URL=postgresql://aiplatform:aiplatform@127.0.0.1:5432/ai_platform_lab
```

未配置或 Postgres 不可达时，Console 显示 **「未接入计费」**（不再使用前端 mock 数字）。

---

## 3. 验证

```bash
# 1. 产生用量（需 LLM_API_KEY）
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hi"}],"model":"chat-fast"}'

# 2. Billing API
curl -s "http://127.0.0.1:8000/internal/billing/usage?hours=720" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq '.items[] | {tenant_id, total_tokens, budget}'

# 3. Console 租户列表（与 billing 一致）
curl -s http://127.0.0.1:8000/internal/tenants \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq '.[] | {tenant_id, tokens_used_this_month, billing_available}'

# 4. 单测
python -m unittest tests.test_console_routes -v
```

---

## 4. 相关文档

- [phase-b-small-production.md](./phase-b-small-production.md) — Token 计量与预算
- [phase-l-console-integration.md](./phase-l-console-integration.md) — Console API 一览
