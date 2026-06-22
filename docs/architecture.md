# AI 中台架构总览

与 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 6 周配套。本仓库是 **教学用最小骨架**，不是生产级多区域部署。

---

## 分层架构

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e', 'secondaryColor': '#21262d', 'tertiaryColor': '#161b22', 'background': '#0d1117', 'mainBkg': '#161b22', 'nodeBorder': '#58a6ff', 'clusterBkg': '#161b22', 'titleColor': '#e6edf3', 'edgeLabelBackground':'#161b22'}}}%%
flowchart TB
  subgraph Client["客户端 / 评测脚本"]
    C1["业务系统 / curl"]
    C2["eval/run.py"]
    C3["eval/agent_run.py"]
  end

  subgraph Edge["接入层 apps/gateway"]
    G1["鉴权 X-Tenant-Id + Bearer"]
    G2["令牌桶限流 + 日配额"]
    G3["Model Router 别名/降级"]
    G4["FastAPI 路由"]
  end

  subgraph Cap["能力层 packages/*"]
    R1["RAG: chunk / embed / retrieve"]
    A1["Agent: tools / session / loop"]
    A2["Phase E: 路由 / 预算 / 质量门 / HITL"]
    O1["Observability: trace / metrics"]
  end

  subgraph Data["数据与外部依赖"]
    Q["Qdrant 向量库"]
    U["上游 LLM OpenAI 兼容 API"]
    F["data/rag 样例文档"]
  end

  C1 --> G1
  C2 --> G1
  C3 --> G1
  G1 --> G2 --> G3 --> G4
  G4 --> R1
  G4 --> A1
  A1 --> A2
  G4 --> O1
  R1 --> Q
  R1 --> U
  R1 --> F
  A1 --> U
  G3 --> U
```

---

## 核心数据流

### 1. Chat 补全（第 1 周 + 第 6 周硬化）

```mermaid
sequenceDiagram
  participant Client
  participant Gateway
  participant Router as Model Router
  participant LLM as 上游 LLM

  Client->>Gateway: POST chat completions
  Gateway->>Gateway: 鉴权、令牌桶、日配额
  Gateway->>Router: 解析别名并选降级链
  Router->>LLM: chat completions
  alt 主模型 5xx 或 429
    Router->>LLM: 尝试 fallback 链下一模型
  end
  LLM-->>Gateway: JSON 响应
  Gateway-->>Client: 200 及 fallback 元数据
```

### 2. RAG 问答（第 2～3 周）

```mermaid
sequenceDiagram
  participant Client
  participant Gateway
  participant RAG
  participant Qdrant
  participant LLM

  Client->>Gateway: POST rag query
  Gateway->>RAG: retrieve top_k
  RAG->>Qdrant: 向量检索
  Qdrant-->>RAG: chunks 与 score
  alt 低置信度未达阈值
    RAG-->>Gateway: 422 RAG_LOW_CONFIDENCE
    Gateway-->>Client: 错误响应
  else 命中
    RAG->>LLM: prompt 与 citations 上下文
    LLM-->>RAG: 生成 answer
    RAG-->>Gateway: answer citations timings
    Gateway-->>Client: 200 响应
  end
```

### 3. Agent 运行时（第 4 周）

```mermaid
sequenceDiagram
  participant Client
  participant Gateway
  participant Agent
  participant Tools
  participant LLM

  Client->>Gateway: POST agent run
  Gateway->>Agent: 合并 session 历史
  loop 至多 max_steps
    Agent->>LLM: messages 与 tools schema
    alt 存在 tool_calls
      Agent->>Tools: 执行工具并校验 ACL
      Tools-->>Agent: 结果写回 messages
    else 最终回复
      Agent-->>Gateway: answer 与 tool_trace
      Gateway-->>Client: 200 响应
    end
  end
```

### 4. Agent 效果进阶（Phase E）

```mermaid
sequenceDiagram
  participant Client
  participant Gateway
  participant Agent
  participant Router as Tool Router
  participant Tools
  participant LLM
  participant Admin as platform_admin

  Client->>Gateway: POST agent run
  Gateway->>Agent: 加载 SessionState
  Agent->>Agent: assemble_llm_messages 预算裁剪
  Agent->>Router: 意图或 RAG 选 Top-K 工具
  Router-->>Agent: candidate_tools
  loop 至多 max_steps
    Agent->>LLM: 裁剪后 messages 与候选 tools
    alt 高风险工具
      Agent-->>Gateway: 202 pending_approval
      Gateway-->>Client: approval_id
      Admin->>Gateway: POST approvals confirm
      Client->>Gateway: resume approval_id
    else 普通工具
      Agent->>Tools: envelope 结果
      Agent->>Agent: quality_gate 低质量则反思 hint
    end
  end
  Gateway-->>Client: tool_trace 与 _platform 元数据
```

Phase E 模块对照：

| 能力 | 包 / 脚本 |
|------|-----------|
| 轨迹评测 | `eval/agent_run.py`、`eval/agent_baseline.jsonl` |
| 工具路由 | `packages/agent/tool_router.py` |
| 上下文预算 | `packages/agent/context_budget.py` |
| 质量门 | `packages/agent/quality_gate.py`、`tool_envelope.py` |
| HITL | `packages/agent/hitl.py`、`apps/gateway/agent/approval_routes.py` |
| Shadow | `packages/agent/shadow.py`（`X-Agent-Shadow: true`） |

---

## 多租户治理矩阵

| 维度 | 配置位置 | 行为 |
|------|----------|------|
| 身份 | `config/tenants.yaml` | `X-Tenant-Id` + Bearer 必须匹配 |
| 模型白名单 | 租户 `allowed_models` | 支持别名（如 `chat-fast`） |
| 默认模型 | 租户 `default_model` | 请求未指定时使用 |
| 工具 ACL | 租户 `allowed_tools` | Agent 工具调用前校验 |
| 工具路由 | `config/agent_tool_routing.yaml` | 意图 / Top-K 缩小候选工具（Phase E） |
| 高风险审批 | `config/tools_marketplace.yaml` | `risk_level: high` → HITL（Phase E） |
| 日配额 | `daily_request_quota` | 进程内 UTC 日切 |
| 速率 | `rate_limit_rps/burst` | 令牌桶，429 `RATE_LIMIT_EXCEEDED` |
| 模型降级 | `config/models.yaml` | 上游失败按链 fallback |

---

## 部署拓扑（Docker Compose）

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e', 'secondaryColor': '#21262d', 'tertiaryColor': '#161b22'}}}%%
flowchart LR
  Host["开发者本机"]
  subgraph Compose["docker compose up（Phase A）"]
    GW["gateway :8000"]
    WK["worker"]
    RD["redis :6379"]
    QD["qdrant :6333"]
    AU["audit.db"]
  end
  LLM["上游 LLM API"]

  Host --> GW
  GW --> RD
  GW --> QD
  GW --> AU
  GW --> LLM
  WK --> RD
  WK --> QD
  WK --> LLM
```

本地也可不用 Docker：`uvicorn` 直连本机 Qdrant（`QDRANT_URL=http://127.0.0.1:6333`）。

---

## 相关文档

- [roadmap.md](./roadmap.md) — 已知限制与后续路线
- [phase-e-agent-quality.md](./phase-e-agent-quality.md) — Phase E 交付说明
- [enterprise-ai-platform-sop.md](./enterprise-ai-platform-sop.md) — 大厂 SOP 与踩坑对照
- [week6-hardening.md](./week6-hardening.md) — 第 6 周验收与演示
- [hardening-build-and-code-guide.md](./hardening-build-and-code-guide.md) — 代码导读
