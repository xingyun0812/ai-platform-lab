# Phase A：可内测硬化

在 [第 6 周学习版](./week6-hardening.md) 之上，把 **状态外置、任务异步、审计落库、CI 门禁** 做到团队内测可用。路线图见 [roadmap.md](./roadmap.md)。

关联 GitHub Issues：[#1 Redis](https://github.com/xingyun0812/ai-platform-lab/issues/1) · [#2 审计](https://github.com/xingyun0812/ai-platform-lab/issues/2) · [#3 Worker](https://github.com/xingyun0812/ai-platform-lab/issues/3) · [#4 CI](https://github.com/xingyun0812/ai-platform-lab/issues/4)

---

## 目标

| 项 | 学习版（第 6 周） | Phase A |
|----|------------------|---------|
| 配额/限流 | 进程内存 | Redis 共享（可回退内存） |
| 索引任务 | gateway BackgroundTasks | Redis 队列 + 独立 worker |
| 审计 | access log | SQLite `audit_events` + 查询 API |
| 评测 | 本地脚本 | CI lint + 冒烟 + baseline 校验 |

---

## 架构

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e', 'secondaryColor': '#21262d', 'tertiaryColor': '#161b22'}}}%%
flowchart TB
  Client["客户端 / eval"]
  GW["gateway :8000"]
  Redis["Redis<br/>quota / ratelimit / queue / task meta"]
  Worker["worker<br/>BLPOP 索引队列"]
  QD["Qdrant"]
  Audit["SQLite audit.db"]
  LLM["上游 LLM"]

  Client --> GW
  GW --> Redis
  GW --> Audit
  GW --> QD
  GW --> LLM
  Worker --> Redis
  Worker --> QD
  Worker --> LLM
```

---

## 一键启动（推荐）

```bash
cd /Users/zhangyue/IdeaProjects/ai-platform-lab
cp .env.example .env   # 可选填 LLM_API_KEY
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
```

Compose 默认启动：**redis + qdrant + gateway + worker**，并设置：

- `REDIS_URL=redis://redis:6379/0`
- `USE_INDEX_WORKER=true`

---

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `REDIS_URL` | 空 | 配置后 quota/限流走 Redis；与 `USE_INDEX_WORKER` 配合任务队列 |
| `USE_INDEX_WORKER` | `false` | `true` 时索引入队，由 worker 消费 |
| `INDEX_QUEUE_NAME` | `ai_platform:index_queue` | Redis 列表名 |
| `AUDIT_ENABLED` | `true` | 关闭则不写 SQLite |
| `AUDIT_DB_PATH` | `./data/audit.db` | 审计库路径 |

**本地零 Redis 模式**（与第 6 周行为一致）：

```bash
# 不配置 REDIS_URL，或显式关闭 worker
USE_INDEX_WORKER=false uvicorn apps.gateway.main:app --reload
```

---

## 演示命令

### 审计查询

```bash
curl -s "http://127.0.0.1:8000/internal/audit/recent?limit=10" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .
```

非 `admin` 租户只能看自己的记录。

### 索引走 Worker 队列

```bash
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'
# 轮询 GET /internal/index/tasks/{task_id}，worker 日志可见 dequeue
```

### 评测与门禁

```bash
python eval/run.py validate-baseline          # 无需服务
python eval/acceptance_smoke.py               # 需 gateway 运行
python eval/run.py run --min-pass-rate 0.85   # 需 Key + 已索引数据
```

---

## CI

`.github/workflows/ci.yml`：

- **lint**：`ruff` + `validate-baseline`
- **smoke**：`docker compose up` → `acceptance_smoke.py` → 审计 API 抽检

PR 合并前自动跑；全量 RAG eval 门禁在本地或 release 时用 `--min-pass-rate`。

---

## 代码导读

| 模块 | 路径 |
|------|------|
| Redis 客户端 | `packages/state/redis_client.py` |
| 配额 | `apps/gateway/quota.py`（内存 / Redis） |
| 限流 | `apps/gateway/rate_limit.py`（内存 / Redis） |
| 任务队列 | `packages/tasks/queue.py` |
| 任务元数据 | `apps/gateway/rag/task_store.py`（内存 / Redis） |
| Worker | `apps/worker/main.py` |
| 审计 | `packages/audit/store.py`、`apps/gateway/audit_routes.py` |

---

## 验收清单

- [ ] `docker compose up -d --build` 后四服务 healthy
- [ ] `demo-b` 连续请求仍触发 `429`（Redis 限速）
- [ ] 提交索引后 worker 日志出现 `dequeued task_id=...`
- [ ] `/internal/audit/recent` 返回刚产生的访问记录
- [ ] `python eval/run.py validate-baseline` 通过
- [ ] `python eval/acceptance_smoke.py` 通过（含 `[PA]` 项）

---

## 已知限制（Phase A 仍非生产）

- 审计为 SQLite 单文件，非集中式日志平台
- 无 Postgres 账单、无密钥托管（Phase B）
- Agent session 仍在内存
- eval 全量门禁需 LLM Key，CI 默认只跑无 Key 冒烟

详见 [roadmap.md](./roadmap.md) Phase B。
