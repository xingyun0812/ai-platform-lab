# Phase C：平台化（供应商 / Region / 租户 API / 工具市场）

Issues：[#11](https://github.com/xingyun0812/ai-platform-lab/issues/11) · [#12](https://github.com/xingyun0812/ai-platform-lab/issues/12) · [#13](https://github.com/xingyun0812/ai-platform-lab/issues/13) · [#14](https://github.com/xingyun0812/ai-platform-lab/issues/14)

> 本阶段提供 **JSON 管理面**，不做完整 LLMOps UI（见 roadmap 非目标）。

---

## 1. 多模型供应商矩阵（#11）

`config/providers.yaml`：价格 / 延迟 / 能力矩阵 + `routing_policy`（`cost|latency|balanced`）。

```bash
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/providers/matrix | jq .
```

`model_router` 按策略从多供应商条目选择 `base_url`，响应 `_platform.provider_id`。

---

## 2. 多 Region 与数据驻留（#12）

`config/regions.yaml` + 租户 `data_zone` / `home_region`。

| Header / 配置 | 作用 |
|---------------|------|
| `X-Region` | 覆盖本次请求的 region |
| `home_region` | 租户默认 region |
| `data_zone` | CN/EU/GLOBAL 驻留约束 |

RAG 路径自动绑定 region → `qdrant_url`（`packages/region/context.py`）。

```bash
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/regions | jq .
```

违规示例：`demo-a`（`data_zone: CN`）+ `X-Region: eu-de` → `403 DATA_RESIDENCY_VIOLATION`。

---

## 3. 租户自助 API（#13）

```bash
# 画像
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/tenants/demo-b/profile | jq .

# admin 调整限额（写入 data/tenant_overrides.json）
curl -s -X PATCH -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"token_budget_daily":800}' \
  http://127.0.0.1:8000/internal/tenants/demo-b/limits | jq .
```

---

## 4. Agent 工具市场 + 审批（#14）

```bash
# 目录
curl -s -H "X-Tenant-Id: demo-a" -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  http://127.0.0.1:8000/internal/tools/marketplace | jq .

# 申请启用 httpbin_delay
curl -s -X POST -H "X-Tenant-Id: demo-a" -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"demo-a","tool_name":"httpbin_delay"}' \
  http://127.0.0.1:8000/internal/tools/requests | jq .

# admin 审批
curl -s -X POST -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/tools/requests/<request_id>/approve | jq .
```

审批通过后 `allowed_tools` 写入 `data/tenant_overrides.json`，下次 `load_tenants()` 生效。

---

## 架构

```mermaid
flowchart TB
  subgraph PC["Phase C 平台层"]
    P["providers.yaml\n矩阵路由"]
    R["regions.yaml\n驻留"]
    T["tenant_overrides.json"]
    M["tools_marketplace.yaml"]
  end
  GW["Gateway"]
  GW --> P
  GW --> R
  GW --> T
  GW --> M

  style PC fill:#1a1a2e,stroke:#a78bfa,color:#e2e8f0
  style GW fill:#1a2a3a,stroke:#60a5fa,color:#e2e8f0
```

---

## 验收

```bash
python eval/acceptance_smoke.py   # 含 PC* 检查
```
