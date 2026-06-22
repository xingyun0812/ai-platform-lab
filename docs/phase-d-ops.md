# Phase D 交付：运维 / 治理 / 控制台 / 效果 / 商业化

Issues：[#15](https://github.com/xingyun0812/ai-platform-lab/issues/15)–[#23](https://github.com/xingyun0812/ai-platform-lab/issues/23)

规划背景：[phase-d-future-evolution.md](./phase-d-future-evolution.md)

构建思路、使用链路与逐文件代码说明见 [phase-d-build-and-code-guide.md](./phase-d-build-and-code-guide.md)。

---

## 波次对照

| 波次 | Issues | 交付 |
|------|--------|------|
| D1 运维 | #15–#17 | 熔断器、Grafana、Prometheus 告警、多实例说明 |
| D2 治理 | #18–#19 | JWT HS256、RBAC、审计 Postgres 双写 |
| D3 控制台 | #20 | `apps/console` → `/console` |
| D4 效果 | #21–#22 | 金丝雀自动回滚、Redis Session、MCP stub |
| D5 商业化 | #23 | `GET /internal/billing/invoice` 分价估算 |

---

## D1 运维

```bash
# 多 Gateway（共享 Redis 配额）
docker compose up -d --scale gateway=2

# 可观测 + Grafana
docker compose --profile observability up -d
# Grafana http://127.0.0.1:3000  (admin/admin)
```

- 熔断：`packages/router/circuit_breaker.py`，连续失败 → `503 CIRCUIT_OPEN`
- 告警：`config/prometheus/alerts.yml`
- 面板：`config/grafana/dashboards/gateway-overview.json`
- Redis 配额探测：`python eval/redis_quota_probe.py`（需 Redis）

---

## D2 治理

```bash
# 可选 JWT（HS256，须与 X-Tenant-Id 一致）
export AUTH_JWT_ENABLED=true
export AUTH_JWT_SECRET=your-dev-secret
```

- 租户 `role`：`config/tenants.yaml`（admin 默认 `platform_admin`）
- RBAC：`packages/auth/rbac.py` — PATCH limits / 工具审批需 `platform_admin`
- 审计：SQLite 保留；`DATABASE_URL` 可达时同步写 `audit_events` 表

---

## D3 控制台 MVP

浏览器打开：`http://127.0.0.1:8000/console/`

静态页调用 internal API（租户画像、矩阵、regions、用量、工具市场）。

---

## D4 效果与工作流

- **自动回滚**：`packages/rag/canary_guard.py` — 最近 `eval/runs/*.json` pass_rate 低于 `CANARY_AUTO_ROLLBACK_MIN_PASS_RATE`（默认 0.85）时，将 `lab-demo` 金丝雀压为 0，写入 `data/canary_guard.json`
- **Session**：`REDIS_URL` 可达时使用 `RedisSessionStore`
- **MCP stub**：`config/mcp_tools.json` → 工具 `mcp_echo`

---

## D5 商业化

```bash
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  "http://127.0.0.1:8000/internal/billing/invoice?month=2026-05" | jq .
```

成本来自 `config/providers.yaml` 示意单价，**非正式发票**。

---

## 验收

```bash
python eval/acceptance_smoke.py   # 含 PD* 检查
```
