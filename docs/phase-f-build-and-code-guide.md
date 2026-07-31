# Phase F 构建思路与代码导读：能力中台补全

> 规格书：[prompt-registry](./phase-f-prompt-registry.md) · [A/B](./phase-f-prompt-experiment.md) · [memory](./phase-f-memory.md) · [MCP](./phase-f-mcp.md) · [compress](./phase-f-context-compress.md)

---

## 目录

1. [构建思路](#1-构建思路)
2. [使用链路](#2-使用链路)
3. [代码导读（按文件）](#3-代码导读按文件)
4. [设计决策](#4-设计决策)
5. [操作命令](#5-操作命令)
6. [自测用例](#6-自测用例)

---

## 1. 构建思路

Phase F 在 Phase E 的 Agent 效果深化之后，做 **能力中台补全**：把平台运行所需的"工具性"基础设施补齐，让 Prompt 版本化、A/B 实验、长记忆、MCP 集成、上下文压缩这些能力从"手写可用"变成"平台原生支持"。

| Issue | 能力 | 核心文件 | 接入方式 |
|-------|------|----------|----------|
| #29 | Prompt 版本化 | `packages/prompt/registry.py` | RAG query / Agent system prompt 渲染时查 registry |
| #30 | Prompt A/B 实验 | `packages/prompt/experiment.py` | registry 内 `render_with_experiment()` 分桶 |
| #31 | 长记忆 | `packages/memory/store.py` | Agent runner 循环中注入 + 自动摘要写入 |
| #32 | MCP 集成 | `packages/mcp/registry.py`, `client.py` | Agent 工具市场注册 MCP 工具 |
| #33 | 上下文压缩 | `packages/agent/context_compress.py` | Agent runner 替换 stub_summarize |

**搭建顺序**：registry → experiment → memory Postgres → MCP registry → context_compress 挂 runner。

**运行时集成图**：

```
RAG query ──→ prompt registry ──→ render_with_experiment() ──→ LLM
                (config/prompts.yaml + data/prompt_overrides.json)

Agent run ──→ context_compress ──→ memory store ──→ inject into messages
              └──→ maybe_compact_with_llm() ──→ LLM summary

Agent tool ──→ tool_catalog ──→ MCP registry ──→ mcp_client.call_tool()
                                 (config/mcp_servers.yaml)
```

### #29 — Prompt 版本化

**问题**：之前 Prompt 是零散的 `.txt` 文件，没有版本管理。运营改了 prompt 后无法回滚，不同租户也无法使用不同 prompt。

**方案**：`PromptRegistry` 管理 `PromptVersion`（draft → active → archived 状态机），双存储层：

| 层 | 路径 | 特性 |
|----|------|------|
| YAML 默认 | `config/prompts.yaml` | git 跟踪，部署自带 |
| JSON overrides | `data/prompt_overrides.json` | 运行时修改，admin API 写入 |

启动时合并：YAML 先加载，JSON 覆盖同 `(tenant_id, prompt_id, version)` 条目。

**模板语法**：`{{var}}` 双花括号（区别于 RAG 模板已有的 `{context}` / `{query}`），`render()` 未提供变量时保持占位符原样，方便开发期定位问题。

**向后兼容**：`_legacy_fallback_get()` 在 registry 找不到 prompt_id 时回退到 legacy `.txt` 文件（version=0 标记）。

### #30 — Prompt A/B 实验

**问题**：运营想试验哪个 prompt 版本效果更好，但平台没有实验框架。

**方案**：`ExperimentStore` 管理 `Experiment`（含 `ExperimentVariant` 列表），核心机制：

```
pick_variant(prompt_id, bucket_key)
  → 查 running 实验
  → SHA256(experiment_id + bucket_key) % 100 确定性分桶
  → 按 variant.percent 累加边界 → 返回命中的 variant
```

**确定性分桶**：同一 `bucket_key`（如 `session_id`）始终分到同一版本，保证用户体验一致。

**自动胜出** `maybe_auto_winner()`：
1. 所有 variant 的 `requests >= min_samples`（默认 100）
2. 按 `success_metric`（quality/latency/tokens）计算每个 variant 得分
3. 第一名与第二名相对差距 >= `winner_margin`（默认 0.1）→ 自动标记 winner 并停止实验
4. **不自动 set_active** — 需 admin 显式 `promote_winner()` 切换到 active

### #31 — 长记忆

**问题**：Agent 对话只在 session 内有 messages，session 结束后历史丢失。用户下次来需要重复上下文。

**方案**：三级作用域 + 双后端 + 自动摘要：

```
MemoryRecord
  tenant_id: str     # 租户隔离
  scope: str         # session | user | tenant
  scope_id: str      # session_id / user_id / tenant_id
  content: str       # 实际内容
  metadata: dict     # turn_count, role, summary 等
```

| 后端 | 条件 | 特点 |
|------|------|------|
| `PostgresMemoryStore` | `DATABASE_URL` 已配置 | JSONB 存储元数据，可扩展语义搜索 |
| `InMemoryMemoryStore` | 无 DATABASE_URL | dict 内存，进程内降级 |

**自动摘要**：每 `MEMORY_SUMMARIZE_EVERY_N_TURNS`（默认 8）轮触发，调 LLM 压缩对话为摘要后写入 memory store。

### #32 — MCP 集成

**问题**：Agent 只能调用内置工具（calc、get_kb_snippet），无法接入外部 MCP 服务器提供的工具。

**方案**：`MCPServerRegistry` + `MCPClient` 双组件架构：

| 组件 | 职责 |
|------|------|
| `MCPServerRegistry` | 管理服务器注册、健康状态、配置持久化 |
| `MCPClient` | JSON-RPC 2.0 协议，transport 层（stdio/http） |

**协议流程**：
```
initialize → notifications/initialized → tools/list → tools/call
```

**工具桥接**：MCP 工具自动转为 `ToolDefinition`，命名 `mcp_{server_id}_{tool_name}`，注册到 Agent 工具市场。

**故障隔离**：单 MCP 服务器失败不影响其他服务器；启动时连接失败仅标记 unhealthy，不阻塞 gateway 启动。

### #33 — 上下文压缩

**问题**：Phase E 的 `stub_summarize` 只是简单字符串拼接，质量差；长记忆注入也没有 token 感知。

**方案**：三层压缩策略：

| 层 | 策略 | 替换了什么 |
|----|------|-----------|
| L1 | 滑窗截断 | 已有（`drop_oldest_until_budget`） |
| L2 | LLM 摘要 | 替换 `maybe_compact_session()` 的 stub |
| L3 | Token 感知注入 | `retrieve_and_inject_memory()` 动态裁剪 |

**降级链**：LLM 摘要失败 → 回退 stub_summarize → 仍然失败 → 跳过压缩。

---

## 2. 使用链路

### 2.1 Prompt 版本管理 + A/B 实验

```mermaid
sequenceDiagram
  participant Admin as Admin
  participant API as REST API
  participant PR as PromptRegistry
  participant ES as ExperimentStore
  participant RAG as RAG query

  Admin->>API: POST prompt/version (create v2)
  API->>PR: create_version()
  PR->>PR: set v2=active, v1=archived
  PR->>PR: _persist() → prompt_overrides.json

  Admin->>API: POST experiment (v1 vs v2 50/50)
  API->>ES: create_experiment()

  RAG->>PR: render_with_experiment(rag_query, session_id)
  PR->>ES: pick_variant(rag_query, session_id)
  ES-->>PR: v1 or v2
  PR-->>RAG: prompt 文本 + 实验元数据
  RAG->>ES: record_request(exp_id, version, latency, tokens)
```

### 2.2 Agent 长记忆 + 上下文压缩

```mermaid
sequenceDiagram
  participant C as Client
  participant R as Agent Runner
  participant M as MemoryStore
  participant LLM as LLM

  C->>R: POST agent run (round 9)
  R->>M: search(session_id, query)
  M-->>R: 相关记忆
  R->>R: inject_memory_into_messages()
  R->>LLM: messages + 记忆
  LLM-->>R: answer
  R->>M: save_memory(本轮摘要)
  R-->>C: answer + _platform.compress

  Note over R: 每 8 轮触发 LLM 摘要压缩
  R->>M: summarize_messages(历史) → summary
  M->>M: save(scope=session, content=summary)
```

### 2.3 MCP 工具调用

```mermaid
sequenceDiagram
  participant A as Agent
  participant MR as MCPServerRegistry
  participant MC as MCPClient
  participant S as MCP Server

  A->>MR: list_tools(server_id)
  MR-->>A: MCPTool list
  A->>MC: connect()
  MC->>S: initialize
  S-->>MC: server_capabilities
  MC->>S: tools/list
  S-->>MC: tool 列表
  A->>MC: call_tool(name, args)
  MC->>S: tools/call
  S-->>MC: result
  MC-->>A: tool output
```

---

## 3. 代码导读（按文件）

### `packages/prompt/render.py`（#29 模板渲染）

**50 行，双花括号模板引擎。**

```python
_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

def render(template, variables=None):
    # 用 _PATTERN.sub 替换 {{var}} 为 variables[var]
    # 未提供变量时保持原样，用于开发期可视化错误

def extract_variables(template):
    # 提取所有变量名，去重保序

def validate_template(template, required_vars):
    # 校验必需变量是否存在，返回缺失列表
```

**为什么不用 `str.format()`？** 因为 RAG 模板已有 `{context}` / `{query}` 占位符，如果再用 `str.format()` 会冲突。`{{var}}` 语法与 Jinja2 / Mustache 一致，学习成本低。

### `packages/prompt/registry.py`（#29 Prompt 版本注册表）

**482 行，核心数据结构与业务逻辑。**

**数据存储**：`_versions: dict[tuple[str, str], dict[int, PromptVersion]]` — 外键 `(tenant_id, prompt_id)`，内键 version。

**加载流程** `load()`：
1. `_merge_yaml()` 解析 `config/prompts.yaml` → 写入 `_versions`
2. `_merge_overrides()` 解析 `data/prompt_overrides.json` → 覆盖同 key 条目
3. 双层合并确保 YAML 是基线，JSON 是运行时覆盖

**关键方法**：

| 方法 | 用途 |
|------|------|
| `get_active(prompt_id)` | 取 active 版本；无 active 则取最新非 draft 版本 |
| `render(prompt_id, variables)` | 渲染 active prompt |
| `render_with_experiment()` | A/B 分桶渲染（#30 集成点） |
| `create_version()` | 新版本，旧 active → archived |
| `set_active()` | 切换 active 版本 |
| `_persist()` | 全量写入 JSON overrides |

**`render_with_experiment()` 工作流**：
```python
def render_with_experiment(prompt_id, variables, *, bucket_key, experiment_store):
    if experiment_store:
        picked = experiment_store.pick_variant(prompt_id, bucket_key)
        if picked:
            exp, variant = picked
            entry = get_version(prompt_id, variant.version)
            return entry.render(variables), entry, exp_info
    # 回退到 active
    return render(prompt_id, variables)
```

**线程安全**：所有读写用 `threading.RLock` 保护。全局单例通过 `init_registry()` / `get_prompt_registry()` 访问。

### `packages/prompt/experiment.py`（#30 A/B 实验）

**563 行，实验生命周期 + 指标收集 + 自动胜出。**

**`pick_variant()` 确定性分桶**：
```python
def pick_variant(prompt_id, *, bucket_key):
    exp = get_running(prompt_id)
    h = SHA256(f"{exp.experiment_id}|{bucket_key}")
    bucket = int(h[:8], 16) % 100
    cumulative = 0
    for v in exp.variants:
        cumulative += v.percent
        if bucket < cumulative:
            return exp, v
```

**指标收集**：
| 指标 | 记录时机 | 存储 |
|------|---------|------|
| `latencies_ms` | 每次 `record_request()` | list，上限 `MAX_LATENCY_SAMPLES=500` |
| `quality_scores` | 每次 `record_quality()` | list，无上限 |
| `tokens_used`, `errors` | 每次 `record_request()` | 累计计数 |

**自动胜出 `maybe_auto_winner()`**：
1. 所有 variant 的 `requests >= min_samples`
2. 按 metric 计算得分（quality 取 avg，latency/tokens 取负 P95/avg，越高越好）
3. `(best - second) / |second| >= winner_margin` → 标记 winner
4. 实验状态变为 `stopped`，不自动 `set_active`

**设计要点**：
- `create_experiment()` 校验 percent 和必须为 100，同 prompt_id 只能有一个 running 实验
- `promote_winner()` 只更新实验状态为 `promoted`，实际 `set_active` 由路由层调用 registry 完成
- 所有状态持久化到 `data/prompt_experiments.json`

### `packages/memory/store.py`（#31 长记忆）

**504 行，三级作用域 + 双后端。**

**`MemoryRecord` 数据结构**：
```python
@dataclass
class MemoryRecord:
    memory_id: str
    tenant_id: str        # 租户隔离
    scope: str            # session | user | tenant
    scope_id: str         # session_id / user_id / tenant_id
    content: str          # 记忆内容或摘要
    metadata: dict        # turn_count, role, summary 等
    created_at: float
```

**`MemoryStore` 协议方法**：
| 方法 | 用途 |
|------|------|
| `save(record)` | 写入一条记忆 |
| `search(tenant_id, scope, scope_id, query, top_k)` | 关键词 + 语义双模式搜索 |
| `list(tenant_id, scope, scope_id)` | 列出全部 |
| `delete(memory_id)` | 删除 |
| `save_summary(tenant_id, scope, scope_id, summary, turn_count)` | 保存对话摘要 |

**搜索实现**（`InMemoryMemoryStore._search`）：
1. 关键词模式：query 分词 → 计算每个 record content 的单词重叠率
2. 语义模式（占位）：余弦相似度 `overlap / sqrt(len(q_tokens) * len(c_tokens))`
3. 按 score 降序取 top_k

**Postgres 表结构**：
```sql
CREATE TABLE IF NOT EXISTS agent_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_memories_lookup ON agent_memories (tenant_id, scope, scope_id);
```

### `packages/memory/summarize.py`（#31 对话摘要）

**138 行，LLM 压缩对话为摘要。**

核心流程：
1. 拼接 messages（含已有 summary 作为 history 前缀）
2. 截断到 `max_input_chars`（默认 4000）
3. 从 prompt registry 加载 `memory_summarize` 模板（`{{history}}` 变量）
4. 调 `forward_with_model_router()` → LLM 生成摘要
5. 失败降级：返回截断后的前 500 字符

### `packages/mcp/registry.py`（#32 MCP 注册表）

**324 行，服务器配置 + 健康管理。**

**`MCPServerConfig` 核心字段**：
```python
@dataclass
class MCPServerConfig:
    server_id: str        # 唯一 ID
    transport: str        # "stdio" | "http"
    command: str | None   # stdio: 子进程命令
    url: str | None       # http: URL
    env: dict             # 环境变量
    headers: dict         # HTTP 头
    api_key: str | None   # API Key（to_dict 时 mask）
```

**`MCPServerRegistry` 方法**：
| 方法 | 用途 |
|------|------|
| `list_servers()` | 列出所有服务器 |
| `get_server(server_id)` | 获取配置 |
| `add_server()` / `remove_server()` | CRUD |
| `list_tools(server_id)` | 懒加载连接 + 拉取工具列表 |
| `mark_healthy()` / `mark_unhealthy()` | 健康状态管理 |

**健康状态**：每服务器独立跟踪（`healthy: bool`），`list_available_servers()` 只返回 healthy 的服务器。

### `packages/mcp/client.py`（#32 MCP JSON-RPC 客户端）

**233 行，协议实现 + 双 transport。**

**生命周期**：
```
connect()
  → stdio: asyncio.create_subprocess_exec(command)
  → http:  httpx.AsyncClient(base_url)
  → initialize (JSON-RPC 2.0)
  → notifications/initialized
  → ready

list_tools()
  → tools/list → [MCPTool(name, description, input_schema)]

call_tool(name, arguments)
  → tools/call → result dict
```

**`MCPTool` 桥接 Agent ToolDefinition**：
```python
@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict      # JSON Schema

    def to_tool_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=f"mcp_{self.server_id}_{self.name}",
            description=self.description,
            parameters=self.input_schema,
        )
```

**故障隔离**：`list_tools()` 异常时只记录日志，返回空列表；单服务器失败不影响其他服务器。

### `packages/agent/context_compress.py`（#33 上下文压缩）

**265 行，三层压缩策略。**

**L2 — `maybe_compact_with_llm()`**：
```python
async def maybe_compact_with_llm(state, *, every_n_turns, keep_recent_turns, tenant_id):
    if 未到压缩轮次 or 轮数不足:
        return state
    turns = split_turns(state.messages)
    old_turns = turns[:-keep_recent_turns]
    recent = turns[-keep_recent_turns:]
    summary = await llm_summarize(old_flat, existing_summary=state.summary, tenant_id=tenant_id)
    return SessionState(messages=recent, summary=summary)
```

**`llm_summarize()`**：调用 `packages.memory.summarize.summarize_messages()`，失败回退 `stub_summarize()`。

**L3 — `retrieve_and_inject_memory()`**：
1. 检查 `budget_remaining >= 200`（token 预算不足则跳过）
2. 调 `MemoryStore.search()` 检索相关记忆
3. 逐条估算 token，动态裁剪以适应 budget
4. 构造 system 消息插入 messages

**`inject_memory_into_messages()`**：将记忆 system 消息插入到 messages 中（默认在 system 块之后）。

**`CompressResult` / `MemoryInjection`** 两个 frozen dataclass 分别携带压缩和注入的元数据，通过 `_platform.compress` / `_platform.memory` 响应字段暴露。

### 读代码顺序

```
packages/prompt/render.py → registry.py → experiment.py →
packages/memory/store.py → summarize.py →
packages/mcp/registry.py → client.py →
packages/agent/context_compress.py
```

---

## 4. 设计决策

| 决策 | 选型 | 理由 |
|------|------|------|
| 模板语法 `{{var}}` 而非 `str.format` | 双花括号正则替换 | 避免与 RAG 模板 `{context}` 冲突 |
| Prompt 双存储层 | YAML + JSON overrides | YAML 基线 git 跟踪，JSON 运行时覆盖，分离基础设施与运行时变更 |
| A/B 分桶用 SHA256 | 确定性哈希 | 同一 session 始终分到同版本，用户体验一致 |
| 自动胜出不自动 set_active | 仅标记 winner | 给 admin 审查机会，防止误胜出直接上线 |
| 记忆三级作用域 | session/user/tenant | 灵活控制记忆可见范围：session 短期、user 长期、tenant 共享 |
| Postgres 记忆存储 | JSONB + 应用层搜索 | 无需引入向量数据库即可工作，搜索降级为关键词 |
| MCP 双 transport | stdio + http | 覆盖本地子进程和远程服务器两种场景 |
| 单 MCP 服务器故障隔离 | 独立健康状态 | 不影响其他服务器和主流程 |
| LLM 摘要降级链 | LLM → stub → 跳过 | 不因摘要失败阻塞 Agent 主循环 |
| Token 感知注入 | 动态裁剪 | 不超 budget，不浪费 tokens |

---

## 5. 操作命令

```bash
# #29 查看 registry 状态
python -c "from packages.prompt.registry import get_prompt_registry; r=get_prompt_registry(); print(r.stats())"

# #29 创建新 prompt 版本（通过 REST API）
curl -s -X POST http://127.0.0.1:8000/internal/prompt/version \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"prompt_id": "rag_query", "content": "使用以下内容回答问题：\n{{context}}\n\n问题：{{query}}", "changelog": "优化指令"}' | jq .

# #29 切换 active 版本
curl -s -X POST http://127.0.0.1:8000/internal/prompt/version/rag_query/2/activate \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #30 创建 A/B 实验（v1 vs v2 50/50）
curl -s -X POST http://127.0.0.1:8000/internal/prompt/experiment \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "prompt_id": "rag_query",
    "variants": [{"version": 1, "percent": 50}, {"version": 2, "percent": 50}],
    "min_samples": 10,
    "success_metric": "quality",
    "winner_margin": 0.1
  }' | jq .

# #30 查看实验指标
curl -s http://127.0.0.1:8000/internal/prompt/experiment/{experiment_id}/metrics \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #30 手动 promote winner
curl -s -X POST http://127.0.0.1:8000/internal/prompt/experiment/{experiment_id}/promote \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #31 写入记忆
curl -s -X POST http://127.0.0.1:8000/internal/memory \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"scope": "session", "scope_id": "sess-001", "content": "用户偏好 Python 3.11"}' | jq .

# #31 搜索记忆
curl -s "http://127.0.0.1:8000/internal/memory/search?scope=session&scope_id=sess-001&q=Python" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #32 列出 MCP 服务器
curl -s http://127.0.0.1:8000/internal/mcp/servers \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #32 添加 MCP 服务器
curl -s -X POST http://127.0.0.1:8000/internal/mcp/servers \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"server_id": "echo", "transport": "stdio", "command": "python", "args": ["-m", "mcp_echo_server"]}' | jq .

# #32 列出 MCP 工具
curl -s http://127.0.0.1:8000/internal/mcp/servers/echo/tools \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #33 启用 LLM 摘要压缩
# 设置环境变量 CONTEXT_LLM_SUMMARY_ENABLED=true（默认）

# #33 查看压缩元数据
# Agent 响应中 _platform.compress 字段

# 验收全部
python eval/acceptance_smoke.py
```

---

## 6. 自测用例

| # | 输入 | 预期 |
|---|------|------|
| 1 | POST prompt version + activate | get_active 返回新版本 |
| 2 | 创建 A/B experiment | 流量分桶；同 session_id 映射到同版本 |
| 3 | 同 query 多次 RAG | 命中的 prompt 版本记录在 `_platform.experiment` |
| 4 | POST /internal/memory + 搜索 | 写入并检索到记忆 |
| 5 | agent run 多轮 | memory 检索注入；`_platform.memory` 显示 injected |
| 6 | CONTEXT_LLM_SUMMARY_ENABLED | 长对话被压缩；`_platform.compress.summary_source` 为 llm |
| 7 | GET /internal/mcp/servers | 服务器列表 |
| 8 | 添加 MCP 服务器 → list tools | 返回 MCP 工具列表 |
| 9 | agent 调用 MCP 工具 | tool_trace 有 `mcp_echo_echo` 等结果 |
| 10 | promote experiment winner | registry 的 active 版本切换；实验状态变 promoted |