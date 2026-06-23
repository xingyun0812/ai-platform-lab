# 本地 LLM 联调配置

> **用途**：把 Gateway / Agent / SDK 接到内网 OpenAI 兼容聚合网关。  
> **安全**：真实 Key 只写在根目录 `.env`（已 gitignore），勿提交仓库。

---

## 上游网关

| 项 | 值 |
|----|-----|
| Base URL | `http://10.212.129.94:8090/v1` |
| 协议 | OpenAI 兼容 `POST /v1/chat/completions` |
| 鉴权 | `Authorization: Bearer <LLM_API_KEY>` |

`.env` 最小配置：

```bash
cp .env.example .env
# 编辑 .env：
LLM_BASE_URL=http://10.212.129.94:8090/v1
LLM_API_KEY=sk-your-key-here
DEFAULT_MODEL=deepseek-v4-flash
AGENT_MODEL=deepseek-v4-flash
RAG_QUERY_MODEL=deepseek-v4-flash
```

---

## 可用模型（已实测 2026-06）

| 上游模型名 | 网关别名 | 场景 |
|-----------|---------|------|
| `deepseek-v4-flash` | `chat-fast` / `chat-fast-v4` | 默认快模型，Demo / Agent / RAG 生成 |
| `deepseek-v4-flash-thinking` | `chat-thinking` / `chat-strong` | 推理增强 |
| `minimax-m2.7` | `chat-minimax` | 备选模型 |

映射定义：`config/models.yaml`  
租户白名单：`config/tenants.yaml`（`demo-a` / `admin` 已包含上述模型）

---

## 快速验证

### 1. 直连上游

```bash
curl -s http://10.212.129.94:8090/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

### 2. 经本仓库 Gateway

```bash
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"model":"chat-fast","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

### 3. 平台冒烟（有 Key）

```bash
export LLM_API_KEY=sk-your-key-here   # 或已在 .env
./eval/platform_demo.sh --with-llm
python eval/sdk_smoke.py
```

---

## 能力边界（重要）

| 能力 | 该网关 | 说明 |
|------|--------|------|
| Chat | ✅ | 三模型均可 |
| Agent | ✅ | 建议 `deepseek-v4-flash`，需 function calling 支持 |
| Embeddings | ❌ | `/v1/embeddings` 返回 `invalid_model` |
| RAG 索引/检索 | ⚠️ | 依赖 embedding；**当前网关下 RAG live 段会失败** |
| Console / 治理 API | ✅ | 不依赖上游 |

**面试 Demo 建议**：

- **有 Key**：Console + Chat + Agent + Audit（跳过或口头说明 RAG embedding 限制）
- **完整 RAG 故事**：需另配 embedding 服务，或等 Phase L 增量方案

---

## 模型别名一览

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'lineColor': '#8b949e'}}}%%
flowchart LR
  U["客户端 model 参数"]
  A["config/models.yaml 别名"]
  G["Gateway 路由"]
  P["10.212.129.94:8090"]

  U --> A --> G --> P

  U -.->|"chat-fast"| A
  A -.->|"deepseek-v4-flash"| G
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [demo-walkthrough.md](./demo-walkthrough.md) | 15 分钟演示 |
| [interview-narrative.md](./interview-narrative.md) | 面试口述 |
| [.env.example](../.env.example) | 全量环境变量 |
