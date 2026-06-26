# Phase L — Console V2 集成跑真（#62-console）

> **状态**：✅ **已完成**（本地实现，待关联 GitHub Issue / PR）  
> **所属优先级**：Phase L 第一优先「Console + 端到端 Demo」的子项  
> **对照**：[phase-l-priority-roi.md](./phase-l-priority-roi.md) · 剩余 Demo/SDK 见 #62、#63

---

## 1. 解决了什么问题？

Phase J 交付了 `console-v2/` 源码，但存在：

| 问题 | 表现 |
|------|------|
| 未 build 或挂载错误 | 访问 `/console/` 看到旧 HTML stub |
| 静态资源路径 | Vite 默认 `/assets/` → Gateway 404 |
| API 路径不一致 | 前端调 `/internal/tenants`，后端无此路由 |
| 列表响应格式 | Agents/Orchestrator 返回 `{agents:[]}` 非裸数组 |

---

## 2. 交付清单

| 项 | 路径 / 说明 |
|----|-------------|
| Vite `base` | `console-v2/vite.config.ts` → `base: "/console/"` |
| React Router basename | `console-v2/src/main.tsx` |
| 静态挂载 | `apps/gateway/main.py` → `apps/console/static` @ `/console` |
| Console 适配 API | `apps/gateway/console_routes.py` |
| 单测 | `tests/test_console_routes.py` |
| 前端 API 修正 | `memory.ts`、`agent.ts`、`orchestrator.ts`、`client.ts` |

### 2.1 新增 REST 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/internal/auth/token` | Console 登录 |
| GET | `/internal/tenants` | 租户列表（admin）；`tokens_used_this_month` 来自 Postgres billing |
| GET | `/internal/metrics` | Dashboard JSON |
| GET | `/internal/settings` | 功能开关只读 |
| GET/POST/DELETE | `/internal/rag/knowledge-bases` | 知识库 CRUD |
| GET/POST | `/internal/rag/knowledge-bases/{id}/documents` | 文档列表/上传 |
| POST | `/internal/rag/query` | 检索测试 |

---

## 3. 构建与访问

```bash
cd console-v2
npm install
npm run build    # 产出 → apps/console/static/

uvicorn apps.gateway.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器：**http://127.0.0.1:8000/console/**

登录：`admin` / `sk-tenant-admin-change-me`（见 `config/tenants.yaml`）

开发模式（热更新）：

```bash
# 终端 1: gateway :8000
# 终端 2:
cd console-v2 && npm run dev
# http://localhost:3000/console/
```

---

## 4. 八页 API 打通情况

| 页面 | 主要 API | 预期 |
|------|----------|------|
| Login | `POST /internal/auth/token` | 200 或 fallback 存 Bearer |
| Dashboard | `GET /internal/metrics` | 200（进程内指标 + 可选 billing） |
| Tenants | `GET /internal/tenants` | 200（admin）；**本月使用** = `get_budget_snapshot` 真实 token，需 `DATABASE_URL` |
| Agents | `GET /internal/agents` + Task Planner | 200（需 `MULTI_AGENT_ENABLED`）；Planner 展示 `final_message`，JSON 默认折叠 |
| RAG | `GET /internal/rag/knowledge-bases` | 200 |
| Memory | `GET /internal/memory/list` | 200（需 `MEMORY_STORE_ENABLED`） |
| Orchestrator | `GET /internal/orchestrator/workflows` | 200 |
| Audit | `GET /internal/audit-actions/actions` | 200 |
| Settings | `GET /internal/settings` | 200 |

RAG **查询/上传** 需 `LLM_API_KEY` + Qdrant；Memory 需 `MEMORY_STORE_ENABLED=true`。

---

## 5. 验证命令

```bash
python3 tests/test_console_routes.py

curl -s http://127.0.0.1:8000/console/ | head -3
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  http://127.0.0.1:8000/internal/metrics | head -c 120
```

---

## 6. 尚未完成（归 #62 / #63）

- [ ] `docs/demo-walkthrough.md` 全链路 live 录制清单
- [ ] `eval/platform_demo.sh` 全绿
- [ ] `eval/sdk_smoke.py` chat/rag/agent 三接口
- [ ] `docs/interview-narrative.md` 10 分钟背诵稿
- [ ] Console 内嵌 RAG 金丝雀 / eval 对比可视化（依赖 #54～#57）

---

## 7. 相关文档

- [demo-walkthrough.md](./demo-walkthrough.md)
- [phase-j-console-v2.md](./phase-j-console-v2.md) — Phase J 原始设计
- [console-tenant-billing.md](./console-tenant-billing.md) — 租户本月用量与 Billing 对接
- [issues-backlog-phase-l.md](./issues-backlog-phase-l.md) — #62-console / #62 / #63
