# 15 分钟平台 Demo 脚本

> **用途**：面试演示、内部分享、验收 Phase L 第一优先「端到端故事」。  
> **前置**：[phase-l-console-integration.md](./phase-l-console-integration.md)（Console 已挂载）  
> **自动化**：`./eval/platform_demo.sh --no-llm`（无 Key）/ `--with-llm`（需 Key + Qdrant）

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

**话术要点**：大厂 SOP = version bump + 金丝雀 + eval 对比再全量；回滚 `canary_percent=0`。

> Phase L #57 将补：pass_rate 跌破阈值自动回滚。

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

> Phase L #59 将补：Orchestrator + HITL vertical 一条 curl 链。

---

## 第 15 分钟：诚实边界 + Q&A

主动说（见 [roadmap.md](./roadmap.md) §已知限制、[interview-narrative.md](./interview-narrative.md)）：

- **模块齐、深度不足**：Rerank stub（#54）、Eval 关键词（#56）、金丝雀无自动回滚（#57）
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

## 一键冒烟

```bash
./eval/platform_demo.sh --no-llm          # 不依赖 LLM
./eval/platform_demo.sh --with-llm        # 需 Key + 索引
python eval/sdk_smoke.py                  # SDK 三接口
```

---

## 相关 Issue

| Issue | 内容 |
|-------|------|
| #62-console | Console 集成 ✅ |
| #62 | 本脚本 + 面试手册完善 |
| #63 | SDK smoke |
| #54～#57 | RAG 深化后本 Demo 更有说服力 |
