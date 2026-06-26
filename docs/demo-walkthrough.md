# 15 分钟平台 Demo 脚本

> **用途**：面试演示、内部分享、验收 Phase L 第一优先「端到端故事」。  
> **前置**：[phase-l-console-integration.md](./phase-l-console-integration.md)（Console 已挂载）  
> **自动化**：`./eval/platform_demo.sh --no-llm`（无 Key）/ `--with-llm`（需 Key + Qdrant）  
> **Live 勾选**（2026-05-19）：见下表 · 闭环 SOP：[closure-sop.md](./closure-sop.md)

| 项 | 命令 | 状态 |
|----|------|------|
| **统一 Live 门禁** | `./eval/live_gate.sh`（无 Key skip / `--require-live` 严格） | 🤖 可自动化 |
| GitHub Live CI | Actions → **Live Gate** + `LLM_API_KEY` secret | 🤖 workflow_dispatch |
| healthz / Console API | `./eval/platform_demo.sh --no-llm` | ✅ |
| chat / index / RAG | `platform_demo --with-llm` | ✅ |
| 二次索引 `skipped_chunks≥1` | `platform_demo --with-llm` | ✅ |
| 反馈飞轮 live | `python eval/feedback_loop_demo.py --live` | ✅ |
| Agent vertical（Orchestrator+HITL） | `eval/agent_vertical_smoke.py --with-llm` | ✅ 7/7 |
| **O1+vertical mock** | `./eval/auto_plan_vertical.sh --mock` | ✅ CI |
| O1+vertical / CoT / 多模态 live | `./eval/live_gate.sh --require-live` | 🤖 见上 |
| Console Plan UI | Agents 页 Task Planner 卡片 | ✅ |

---

## 环境准备（第 0 分钟）

> **内网 LLM 网关**：见 [local-llm-setup.md](./local-llm-setup.md)（Base URL、三模型别名、`.env` 模板）。

```bash
cd /path/to/ai-platform-lab
cp .env.example .env   # 填 LLM_BASE_URL + LLM_API_KEY（见 local-llm-setup.md）

# 推荐 Compose（Qdrant + Redis + Postgres）
docker compose up -d qdrant redis postgres

# 或最小：仅 Gateway
uvicorn apps.gateway.main:app --reload --host 127.0.0.1 --port 8000

# Console 静态资源（若未 build）
cd console-v2 && npm install && npm run build && cd ..
```

| 变量 | 演示需要 |
|------|----------|
| `LLM_BASE_URL` | 默认 `http://10.212.129.94:8090/v1`（见 local-llm-setup） |
| `LLM_API_KEY` | Chat / Agent 需要；**该网关无 embeddings，RAG 索引 live 段会失败** |
| `MEMORY_STORE_ENABLED=true` | Memory 页有数据 |
| `MULTI_AGENT_ENABLED=true` | Agents 页（默认通常开） |
| `WEB_SEARCH_MODE=ddg` | Agent 真实联网搜索（天气/新闻 demo）；CI 用 `mock` |
| `DATABASE_URL` | 租户页 **本月使用**、Dashboard token 统计（见 [console-tenant-billing.md](./console-tenant-billing.md)） |

**推荐模型别名**：`chat-fast`（deepseek-v4-flash）、`chat-thinking`、`chat-minimax`

租户：`admin` / `sk-tenant-admin-change-me`

---

## 叙事线（讲给别人听）

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e'}}}%%
sequenceDiagram
  participant U as 你/面试官
  participant C as Console
  participant G as Gateway
  participant Q as Qdrant

  U->>C: 登录 admin
  C->>G: 鉴权 + 租户
  U->>G: 索引 kb v1
  G->>Q: embed + upsert
  U->>G: RAG 查询 v1
  U->>G: 索引 v2 + 金丝雀 30%
  U->>G: eval compare v1 vs v2
  U->>C: Dashboard 用量 / Audit 轨迹
```

---

## 第 1～3 分钟：Console 登录与总览

1. 打开 **http://127.0.0.1:8000/console/**
2. 登录 `admin` / `sk-tenant-admin-change-me`
3. **Dashboard**：说明多租户指标、`/internal/metrics`
4. **Settings**：功能开关只读（sandbox/oauth2/memory 等）

**话术要点**：这是平台运营面，不是业务 App；鉴权是 `X-Tenant-Id` + Bearer。

---

## 第 4～7 分钟：RAG v1 索引与查询

### 4.1 索引 v1（curl，可同时在 Console RAG 页看知识库）

```bash
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}'
```

轮询任务至 `succeeded`（记下 `task_id`）：

```bash
curl -s http://127.0.0.1:8000/internal/index/tasks/<task_id> \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

### 4.2 查询 v1

```bash
curl -s http://127.0.0.1:8000/v1/rag/query \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","kb_id":"lab-demo","version":1,"query":"RAG 数据管道是什么"}'
```

或在 Console **RAG** 页选 `lab-demo` → 查询测试。

**话术要点**：`kb_id + version` 可回放；低分可拒答（`min_score`）。

---

## 第 8～11 分钟：v2 + 金丝雀 + eval 对比（SOP 核心）

### 8.1 索引 v2

```bash
# 使用 samples/hello-v2.txt（若存在）或改 source_uri
curl -s http://127.0.0.1:8000/internal/index \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"kb_id":"lab-demo","version":2,"source_uri":"samples/hello-v2.txt"}'
```

### 8.2 开金丝雀（`config/rag.yaml`）

```yaml
kb_routing:
  lab-demo:
    stable_version: 1
    canary_version: 2
    canary_percent: 30   # 演示用；回滚设 0
```

重启 Gateway 或热加载配置后：

```bash
curl -s http://127.0.0.1:8000/internal/kb/lab-demo/routing \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

### 8.3 eval 对比（需 Key）

```bash
python eval/run.py run --run-id demo-v1
# 调整金丝雀或版本后
python eval/run.py run --run-id demo-v2
python eval/run.py compare eval/runs/demo-v1.json eval/runs/demo-v2.json
```

**话术要点**：大厂 SOP = version bump + 金丝雀 + eval 对比再全量；回滚 `canary_percent=0` 或自动回滚 Job（#57 ✅）。

---

## 第 12～14 分钟：治理与 Agent（可选）

### Audit

Console **审计** 页或：

```bash
curl -s "http://127.0.0.1:8000/internal/audit/recent?limit=10" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

### Agent（需 Key）

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"messages":[{"role":"user","content":"用 calc 算 12*8"}],"session_id":"demo-1"}'
```

Console **Agents** 页可看注册 Agent / 委托（`MULTI_AGENT_ENABLED`）。

**Task Planner 演示**（同页下方卡片）：

- `auto_plan 执行` 后，**执行结果**卡片展示 `final_message`（自然语言答复）
- **执行结果 JSON** 默认折叠，调试时再展开
- 联网搜索：`.env` 设 `WEB_SEARCH_MODE=ddg`，用 `admin` 租户，goal 示例见 [console-agent-planner.md](./console-agent-planner.md)

> Phase L #59：见 [demo-agent-vertical.md](./demo-agent-vertical.md)（Orchestrator + HITL vertical curl 链，live 6/6 ✅）。

### Agent JD2 五分钟路线（Phase O）

无 Key 离线矩阵（CI 同款）：

```bash
python eval/agent_jd2_gate.py run
./eval/data_analysis_vertical.sh --mock
./eval/auto_plan_vertical.sh --mock
python eval/agent_planner_smoke.py
python eval/agent_cot_smoke.py
```

有 Key 时可追加：

```bash
# Task Planner — 仅生成 Plan
curl -s http://127.0.0.1:8000/v1/agent/plan \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","goal":"查资料并计算 1+2"}'

# O1 + Vertical 端到端 — auto_plan 驱动 web_search → sql → calc
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id":"admin",
    "session_id":"jd2-auto-plan-vertical",
    "auto_plan":true,
    "goal":"数据分析：搜索 AI analytics 背景，对 demo_sales SQL 聚合，再 calc 算同比"
  }'

# 离线验收（CI 同款，无需 Gateway）
./eval/auto_plan_vertical.sh --mock

# CoT 推理
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"tenant_id":"admin","messages":[{"role":"user","content":"解释 2+2"}],"session_id":"jd2-cot","reasoning_mode":"cot"}'

# Multi-Agent 黑板（委托后）
curl -s http://127.0.0.1:8000/v1/agent/blackboard/jd2-demo \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

**话术要点**：Phase O 把 JD2 §4.1 从「ReAct 能调工具」升级为 **可规划、可显式推理、可协作、可接搜索/SQL、可跑 vertical**；**O1 + vertical 闭环**见 `./eval/auto_plan_vertical.sh`；门禁见 `eval/agent_jd2_gate.py`。

### Phase M 增量索引（#63～#66）

`./eval/platform_demo.sh --with-llm` 会自动：

1. 首次索引 `samples/hello.txt`
2. **二次索引**同一文件 → 任务 API 返回 `skipped_chunks >= 1`
3. RAG query（`min_score=0.2` 适配 demo 向量分）

```bash
# 手动看任务详情
curl -s -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  "$BASE_URL/internal/index/tasks/<task_id>" | jq '{status, new_chunks, updated_chunks, skipped_chunks}'
```

Console 删除文档会调用 purge-source，同步清理 Qdrant + BM25（见 [phase-m-incremental-index.md](./phase-m-incremental-index.md)）。

---

### 反馈飞轮（#61）

```bash
python eval/feedback_loop_demo.py --live --base-url http://127.0.0.1:8000
```

点踩 2 条 → `cycle` → 得到 `suggestion_id`（详见 [phase-l-feedback-loop-e2e.md](./phase-l-feedback-loop-e2e.md)）。

---

## 第 15 分钟：诚实边界 + Q&A

主动说（见 [roadmap.md](./roadmap.md) §已知限制、[interview-narrative.md](./interview-narrative.md)）：

- **仍浅**：细粒度 RBAC、生产 DLP（**增量索引 Phase M 已做满**）
- **opt-in 默认关**：沙箱、OAuth2、语义缓存、Memory Store
- 多 AZ/GPU 为 Helm 模板级验证
- 无真实支付/发票

---

## SDK 段落（#63）

```bash
pip install -e sdk/python   # 或 PYTHONPATH=sdk/python

python eval/sdk_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --tenant admin \
  --api-key sk-tenant-admin-change-me
```

无 `LLM_API_KEY` 时 chat/rag/agent 会 **优雅 skip**（仍 exit 0）；有 Key 时三接口都会尝试。

`AgentResource.run` 需传 `tenant_id` + `messages`（见 `eval/sdk_smoke.py` 示例）。

---

## Phase Q 段落（#116～#121 + Q7 Graph Runtime）

**Tag**：`phase-q-advanced-planning`（Q1～Q6）· `phase-q-graph-runtime`（Q7 + Console HITL + Redis checkpoint）

**离线门禁**（无 LLM Key）：

```bash
python eval/plan_quality_gate.py run
python eval/agent_jd2_gate.py run    # 含 Q6 plan_quality + Phase Q 单测
```

**Live E2E**（需 Gateway + `.env` 中 `LLM_API_KEY`）：

```bash
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000
python eval/phase_q_live.py          # 见 eval/live_gate.py 统一入口
python eval/live_gate.py run --require-live
```

| 检查项 | 是否需要 Key | 说明 |
|--------|-------------|------|
| `phase_q_plan_export_live` | 否 | Q5 `POST /v1/agent/plan/export` |
| `phase_q_orchestrator_checkpoint_live` | 否 | Q7 执行 `data-analysis-vertical` + GET checkpoint |
| `phase_q_plan_generate_live` | 是 | Q1 `POST /v1/agent/plan` |
| `phase_q_plan_approval_resume_live` | 是 | Q4 审批 + `plan_approval_id` resume |

---

## 一键冒烟

```bash
./eval/platform_demo.sh --no-llm          # 不依赖 LLM（含 feedback mock + agent vertical）
./eval/platform_demo.sh --with-llm        # 含二次索引 skipped 断言 + RAG live
python eval/agent_jd2_gate.py run         # Phase O JD2 + Phase Q 离线矩阵
python eval/live_gate.py run              # Live E2E（含 Phase Q export/plan/approval）
python eval/multimodal_embedding_gate.py run  # Phase P 多模态 Embedding 离线矩阵
python eval/acceptance_smoke.py --platform-demo
python eval/sdk_smoke.py                  # SDK 三接口
```

---

## 相关 Issue

| Issue | 内容 |
|-------|------|
| #63～#66 | Phase M 增量索引 ✅ live |
| #62-console | Console 集成 ✅ |
| #62 | 本脚本 + 面试手册 | ✅ live 已验 |
| #63 | SDK smoke |
| #54～#57 | RAG 深化后本 Demo 更有说服力 |
