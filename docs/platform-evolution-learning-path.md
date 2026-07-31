# AI Platform Lab 平台演进学习手册

> 本文档是本 session 的对话提炼，按平台演进的阶段串联成一个连贯的学习路径。
> 从 RAG 数据管道开始，到查询服务化、Agent 运行时、观测与评测，再到工程硬化、并行交付与平台化。
> 适合新加入的开发者或想系统理解平台设计的读者。

---

## 目录

1. [RAG 数据管道（第 2 周）—— 文档进知识库](#1-rag-数据管道第-2-周--文档进知识库)
2. [RAG 查询服务化（第 3 周）—— 带质量底线的对外 API](#2-rag-查询服务化第-3-周--带质量底线的对外-api)
3. [Agent 运行时骨架（第 4 周）—— ReAct 循环与工具系统](#3-agent-运行时骨架第-4-周--react-循环与工具系统)
4. [观测与评测回归（第 5 周）—— Tracing + Metrics + Eval](#4-观测与评测回归第-5-周--tracing--metrics--eval)
5. [Phase A：可内测硬化](#5-phase-a可内测硬化)
6. [Phase B2：并行交付](#6-phase-b2并行交付)
7. [Phase B：生产就绪增强（B1～B3）](#7-phase-b生产就绪增强b1b3)
8. [Phase C：平台化（#11～#14）](#8-phase-c平台化1114)
9. [Phase D：运维与治理（#15～#23）](#9-phase-d运维与治理1523)
10. [Phase E：Agent 效果深化（#24～#28）](#10-phase-eagent-效果深化2428)
11. [Phase F：能力中台补全（#29～#33）](#10-phase-f能力中台补全2933)
12. [Phase G：模型服务增强（#34～#35）](#11-phase-g模型服务增强3435)
13. [Phase H：Agent 高阶能力（#37～#40）](#12-phase-hagent-高阶能力3740)
14. [Phase I：安全与合规（#41～#44）](#13-phase-i安全与合规4144)
15. [Phase J：平台开发者体验（#45～#48）](#14-phase-j平台开发者体验4548)
16. [Phase K：生产基础设施（#49～#52）](#15-phase-k生产基础设施4952)
17. [演进脉络总结](#16-演进脉络总结)

---

## 1. RAG 数据管道（第 2 周）—— 文档进知识库

### 目标

把「文档进知识库」做成平台能力：指定路径或上传 → 异步任务 → chunk → embed → 写入 Qdrant。

### 架构

```
输入（source_uri / multipart 上传）
  → POST /internal/index
  → BackgroundTasks（异步）
  → chunker（切块）
  → embeddings（调 LLM /embeddings）
  → vector_store → Qdrant（写入）
```

### 关键文件

| 路径 | 职责 |
|------|------|
| `apps/gateway/rag/routes.py` | HTTP 路由入口 |
| `apps/gateway/rag/pipeline.py` | 索引编排（薄 re-export） |
| `packages/rag/index_pipeline.py` | 实际的 run_index_task |
| `packages/rag/chunker.py` | 文档切块策略 |
| `packages/rag/vector_store.py` | Qdrant 写入与检索 |

### 设计要点

- **异步任务**：POST 立即返回 `task_id: pending`，调用方轮询 `GET /internal/index/tasks/{id}` 直到 `succeeded`
- **kb_id + version**：每个知识库可索引多个版本，支持版本回放和金丝雀发布
- **Embedding 直连**：gateway 内的 embeddings 模块直连上游 LLM `/embeddings` 接口，不经过 gateway 自身路由

### 用户视角 vs 内部 API

`/internal/index` 是平台内部编排接口，用户不直接调用它。用户通过 Console 管理后台操作：

| 用户操作 | 背后触发的 API |
|----------|---------------|
| 点「上传文档」→ 选文件 → 点「提交」 | `POST /internal/index/upload` |
| 点「同步知识库」或「刷新」 | `POST /internal/index`（带 `source_uri`） |
| 在 Chat UI 提问 | `POST /internal/retrieve` |

用户只感知到「文档传上去了」和「问答有结果了」，不会看到 `task_id`、`chunks_indexed` 这些细节。
这个模式与云厂商（AWS Bedrock、阿里云百炼）一致。

---

## 2. RAG 查询服务化（第 3 周）—— 带质量底线的对外 API

### 目标

对外 `POST /v1/rag/query`：检索 → 拼上下文 → LLM 回答；空检索/低分拒答；返回引用与分阶段耗时。

### 整体链路

```
POST /v1/rag/query
  → apps/gateway/rag/query_routes.py          ← 路由入口
  → apps/gateway/rag/query_service.py          ← query 编排
    ├─ packages/rag/vector_store.py             ← Qdrant 向量检索
    ├─ 本地 min_score 过滤                      ← 质量底线
    ├─ config/rag_prompt.txt                    ← 拼 prompt 模板
    └─ forward_with_model_router                ← 调 LLM 做回答
```

### 三个执行阶段

**阶段 1 — 检索（retrieve）**

`query_service.py:run_rag_query()` 从 Qdrant 召回 top_k 条结果，每条带 `{chunk_id, source_uri, text, score}`。如果请求没传 `version`，自动查 Qdrant 中该 `kb_id` 的最大版本。

**阶段 2 — 质量过滤（min_score）**

| 情况 | 结果 |
|------|------|
| 检索零条 | HTTP 422, `RAG_NO_EVIDENCE` |
| 有候选但最高分 < min_score | HTTP 422, `RAG_LOW_CONFIDENCE` |
| 指定 kb_id 从未索引过 | HTTP 422, `RAG_KB_NOT_FOUND` |
| 通过过滤 | 继续走 LLM |

`min_score` 是 embedding 向量余弦相似度的硬阈值。这是纯检索阶段的轻量拒答，不依赖 LLM 做二次判断，成本低、响应快。

**阶段 3 — LLM 回答**

把过滤后的 chunks 按 score 降序拼接成上下文，填入 `config/rag_prompt.txt` 模板中的 `{context}` 和 `{query}`，构造 chat 请求转发给上游 LLM。返回 `answer` + `citations` + `timings`。

### 关键设计决策

| 决策 | 原因 |
|------|------|
| 拒答不用 LLM 判断 | 成本低、响应快。纯靠相似度阈值 |
| 配额在 LLM 前扣减 | 拒答不消耗配额 |
| citations + timings | 可追溯、可观测 |
| 省略 version 自动用最新 | 用户不用关心版本号 |
| prompt 从文件读 | 不改代码就能调 prompt |

---

## 3. Agent 运行时骨架（第 4 周）—— ReAct 循环与工具系统

### 目标

平台向 Agent 能力：工具注册表、租户工具白名单、最大步数/工具超时/重试、内存会话。

### 整体链路

```
POST /v1/agent/run
  → apps/gateway/agent/routes.py         ← HTTP 路由 + 配额/鉴权
  → packages/agent/runner.py             ← run_agent(): session 组装 + 编排
  → packages/agent/react_loop.py         ← run_react_loop(): LLM ↔ 工具循环
    ├─ forward_with_model_router          ← 调上游 LLM /chat/completions
    ├─ execute_tool()                    ← 单工具执行（超时/重试/HITL）
    └─ packages/agent/registry.py        ← 工具注册表
```

### run_agent() 编排（runner.py:252）

`run_agent()` 在进入 ReAct 循环前做 6 件事：

1. **解析推理模式** — `resolve_reasoning_mode()` 决定走 react 还是 cot
2. **解析工具调用策略** — 决定串行还是并行执行工具（Phase Q 扩展）
3. **模型白名单校验** — `is_model_allowed()` 检查租户是否可用该模型
4. **Session 组装** — 从 SessionStore 取出历史，`assemble_llm_messages()` 在 token budget 内保留摘要 + 最近 N 轮
5. **Memory 注入** — 如果开启，用用户最新问题检索持久化的 Memory 记录（Phase F 扩展）
6. **工具路由** — 只暴露租户白名单内的工具给 LLM

### run_react_loop() 核心循环（react_loop.py:439）

```
while steps < AGENT_MAX_STEPS:
    1. forward_with_model_router(payload)         ← 调 LLM
    2. LLM 返回：
       ├─ finish_reason == "tool_calls" → 执行工具 → 结果加回 messages → continue
       └─ finish_reason == "stop"        → 拿到 final_message → break
    3. 超步数 → raise AGENT_MAX_STEPS
```

**上下文预算管理**（react_loop.py:477-492）：每轮开始前检查 token 是否超 budget，超了用 `drop_oldest_until_budget()` 丢弃最早的消息，保留下文完整性。

### execute_tool() 工具执行（react_loop.py:82）

```
1. 检查租户是否授权 → 否: AGENT_TOOL_FORBIDDEN
2. 检查工具是否存在 → 否: AGENT_TOOL_NOT_FOUND
3. 解析参数 JSON → 非法: AGENT_TOOL_BAD_ARGS
4. Shadow mode? → 返回 mock 记录
5. HITL 检查？→ 走人工审批流程（Phase H）
6. 执行循环（重试 + 超时）:
   - asyncio.wait_for(tool.handler(args), timeout)
   - 超时 → 重试（最多 AGENT_TOOL_MAX_RETRIES 次）
   - 成功 → 记录耗时 + 审计日志
   - 全部失败 → 返回 failed 记录（不崩，LLM 看到 error 后可继续）
```

工具超时**不会让整个请求崩溃**，这是关键设计——LLM 收到 error 消息后可以决定下一步。

### 工具注册表（registry.py）

`ToolRegistry` 是一个内存注册表，核心方法：

| 方法 | 作用 |
|------|------|
| `get(name)` | 按名字取工具定义 |
| `list_for_tenant(allowed)` | 返回租户可见的工具列表 |
| `openai_tools_spec(allowed)` | 转成 OpenAI function calling 格式 |
| `is_allowed(name, allowed)` | 检查工具是否在租户白名单 |

内置工具：

| 工具 | handler | 说明 |
|------|---------|------|
| `calc` | `handle_calc` | 安全 AST 解析，只允许 `+ - * /`，避免 eval 注入 |
| `get_kb_snippet` | `handle_get_kb_snippet` | 调 RAG 检索接口 |
| `httpbin_delay` | `handle_httpbin_delay` | 纯测试用 |
| `web_search` | `handle_web_search` | 真实联网搜索（Phase O） |
| `sql_query` | `handle_sql_query` | 只读 SELECT（Phase O） |

### resolve_reasoning_mode() 实现（reasoning.py:19-23）

```python
def resolve_reasoning_mode(request_mode: str | None, settings_mode: str | None) -> str:
    raw = (request_mode or settings_mode or "react").strip().lower()
    if raw not in {"react", "cot"}:
        raise ReasoningModeError(...)
    return raw
```

三种取值来源（优先级从高到低）：API 请求体 → 配置文件 → `"react"` 兜底。
合法值只有 `"react"` 和 `"cot"`。`cot` 模式的实际行为在调用方：
- `merge_cot_system_prompt()` 在 system prompt 追加 CoT 指令，要求模型用 `<thinking>...</thinking>` 写出推理
- `apply_cot_to_assistant_message()` 用正则剥离 thinking，存入 `reasoning_trace`，对用户可见的正文删掉 thinking 标签

### 关键设计决策

| 决策 | 原因 |
|------|------|
| 租户白名单在路由层过滤 | LLM 看不到禁用工具，更安全 |
| 工具超时不崩请求 | 让 LLM 基于 error 做下一步 |
| 上下文预算用丢弃策略而非截断 | 保留下文完整性 |
| Session 在内存中 | 第 4 周骨架阶段，不依赖外部存储 |
| ReAct 同步 | 步数有限（默认 8），耗时可控 |
| model_router 自动降级 | 避免单一模型故障导致不可用 |

---

## 4. 观测与评测回归（第 5 周）—— Tracing + Metrics + Eval

第 5 周不产生新业务能力，而是给平台装上「仪表盘」和「质检」。三个独立模块：

```
第 5 周
  ├─ Tracing   — OpenTelemetry span，跟踪请求走了哪些组件
  ├─ Metrics   — Prometheus 计数 + P95，监控系统健康度
  └─ Eval      — baseline.jsonl 打分 + compare，防止改坏东西
```

### Tracing（otel.py）

`init_otel()` 创建 OTel 的 `TracerProvider`，支持两种导出方式：

| 导出方式 | 用途 |
|---------|------|
| ConsoleSpanExporter | 开发阶段，span 直接打日志 |
| OTLPSpanExporter | 生产，推给 Jaeger/Tempo（Phase B2 加入） |

`component_span` context manager 在关键路径上打 span：

```python
with component_span("agent.run", component="agent", enabled=True):
    result = await execute_agent_graph(...)
```

span 自带 `component` 和 `app.trace_id` 两个属性，支持 W3C `traceparent` 传播（分布式追踪）。

第 5 周只打了 4 个粗粒度 span：`http.request` / `gateway.chat_completions` / `rag.query` / `agent.run`。

### Metrics（metrics.py）

全部进程内内存，不加外部依赖。`MetricsStore` 用 `threading.Lock` 保证并发安全。

`GET /metrics` 返回 Prometheus 文本：

```
http_requests_total{path="/v1/rag/query",tenant_id="admin",status="200"} 42
http_request_duration_ms_p95{path="/v1/rag/query",tenant_id="admin"} 1250.50
```

按 `path + tenant_id + status` 三个维度分片。延迟样本有上限（默认 2000），防止内存泄漏。

还有 `dashboard_snapshot()` 方法，给 Console Dashboard 用的 JSON 快照（Phase L 集成）。

### Eval（run.py）

**评测流程**：

```
baseline.jsonl（35 条用例）
  → 遍历每条，调 POST /v1/rag/query
  → 每条判 pass/fail（evaluate_case）
  → 汇总：total / passed / failed / pass_rate
  → 写入 eval/runs/{run_id}.json
```

两种用例模式：

| expect | 判据 |
|--------|------|
| `hit` | HTTP 200 + answer 含有预期关键词 |
| `refuse` | HTTP 422 + error.code 在允许列表内 |

**compare**（compare_reports()）：取两次报告的 pass_rate 算 delta，同时找出**翻转用例**（从 pass→fail 或 fail→pass）。翻转用例比整体通过率更有价值——它们告诉你"具体哪个场景被改坏了或修好了"。

**验收场景**：故意把 prompt 改成乱答指令 → 跑 eval → 通过率下降 → compare 看到 flip。

Phase J 扩展了 `run-eval`（完整 EvalPipeline）和 `gate`（CI 门禁对比，阈值默认 5%）。

---

## 5. Phase A：可内测硬化

### 目标

把第 6 周学习版的"进程内存 + BackgroundTasks"换成"Redis + Worker + SQLite"，让平台能扛团队内测。

### 四个升级

| 维度 | 学习版（第 6 周） | Phase A | 为什么 |
|------|-----------------|---------|--------|
| 配额/限流 | 进程内存字典 | Redis 共享 | 多 gateway 实例需要一致的状态 |
| 索引任务 | gateway BackgroundTasks | Redis 队列 + 独立 worker | 索引是 CPU/IO 密集型 |
| 审计 | access log 文本 | SQLite audit_events + 查询 API | 内测需要可追溯的操作记录 |
| 评测 | 本地手动跑 | CI lint + 冒烟 + baseline | 防止 PR 合入前改坏东西 |

### 架构变化

```
旧：
Client → Gateway → Qdrant + LLM
                  └─ BackgroundTasks（索引）

新：
Client → Gateway → Redis ← Worker（索引）
                  └─ SQLite（审计）
```

### 四个模块

**1. 配额/限流 — 内存 vs Redis 双实现**

`apps/gateway/quota.py` 和 `rate_limit.py` 各有两种后端。启动时检测 `REDIS_URL` 环境变量：配置了就 Redis（共享计数 + TTL），否则回退内存。

**2. 任务队列 — packages/tasks/queue.py + apps/worker/main.py**

gateway 收到索引请求 → `RPUSH` 到 Redis 列表 → 写入 Redis hash 记录 `pending` → 返回 `task_id`。worker 用 `BLPOP` 阻塞监听 → 取出执行 `run_index_task()` → 更新状态为 `success/failed`。

`task_store.py` 抽象了"内存/Redis"两种后端，调用方总是 `GET /internal/index/tasks/{id}`。

**3. 审计 — packages/audit/store.py**

每次关键操作写一条记录到 SQLite：`tenant_id`、`event_type`、`detail`、`created_at`。
`GET /internal/audit/recent` 支持按租户过滤，非 admin 只能看自己的。

**4. CI 门禁 — .github/workflows/ci.yml**

```
lint  → ruff + validate-baseline
smoke → docker compose up → acceptance_smoke.py → 审计 API 抽检
```

全量 RAG eval 需要 LLM Key，CI 默认只跑无 Key 冒烟。

### 兜底设计

不设 `REDIS_URL` 时一切回退到进程内存——Redis 和 Worker 都是可选的增强，不是硬依赖。延续了第 5 周 OTel「功能按需启用」的哲学。

---

## 6. Phase B2：并行交付

三个独立功能并行开发，互不依赖。

```
Phase B2
  ├─ #7  密钥托管       — 租户密钥从环境变量 → Vault
  ├─ #8  混合检索       — 纯向量 → 向量 + BM25 + RRF 融合
  └─ #10 可观测栈       — Console 日志 → Jaeger + Prometheus
```

### 1. 密钥托管（#7）

`SecretsProvider` 抽象，两种实现：

| 模式 | 配置 | 行为 |
|------|------|------|
| `env` | `SECRETS_PROVIDER=env` | `bearer_secret_ref` → 环境变量查询 |
| `vault` | `SECRETS_PROVIDER=vault` | HashiCorp Vault KV v2 读取 |

租户加载时 `bearer_secret_ref` 通过 `SecretsProvider.resolve(ref)` 取值，具体从 env 还是 Vault 取决于当前 provider。Vault 需要 `docker compose --profile vault up -d`。

### 2. RAG 混合检索（#8）

```
配置：config/rag.yaml 中 retrieval_mode: hybrid

检索时：
向量检索 top_k  →   ┐
                    ├→  RRF 融合 → 排序结果
BM25 检索 top_k  →   ┘
```

BM25 索引在索引阶段写入 JSON 文件 `data/rag/bm25/{kb_id}/v{version}.json`。
RRF（Reciprocal Rank Fusion）对两条排序列表的排名取倒数求和，不需要调权重。
timings 拆成 `retrieve_vector_ms` / `retrieve_bm25_ms` / `fusion_ms`。

代码：`packages/rag/bm25_index.py`、`packages/rag/hybrid.py`、`packages/rag/retrieval.py`。

### 3. 可观测栈（#10）

`docker compose --profile observability up -d` 启动：

| 服务 | 端口 | 用途 |
|------|------|------|
| Jaeger | 16686 | 分布式追踪 UI |
| Prometheus | 9090 | 从 gateway `/metrics` 拉指标 |
| otel-collector | 4317 | 接收 OTLP，转发给 Jaeger |

第 5 周的 `/metrics` 端点和 OTel span 一直存在，Phase B2 提供了"数据消费者"——Jaeger 看 span，Prometheus 拉指标。

---

## 7. Phase B：生产就绪增强（B1～B3）

Phase B 在 [Phase A](#5-phase-a可内测硬化) 的硬化底座上做 **生产就绪增强**，三个子阶段并行交付，互不依赖。

```
Phase B
  ├─ B1   Token 计量与租户预算  — Postgres 记录 + 日/月预算拦截
  ├─ B2   密钥/混合检索/可观测  — Vault + BM25 RRF + Jaeger/Prometheus
  └─ B3   Rerank + KB 金丝雀   — 检索后重排 + 版本灰度路由
```

---

### 7.1 B1 — Token 计量与租户预算（#5/#6）

#### 目标

每次 Chat 调用记录 token 用量，支持租户日/月预算硬拦截。解决"谁用了多少 token，花多少钱"的问题。

#### 整体链路

```
Chat 请求
  → request_guards.py:check_token_budget()
    → budget.py:is_budget_exceeded()
      → store.py:sum_tokens() 查 Postgres 当日/月聚合

Chat 响应
  ← recorder.py:record_upstream_usage()
    → usage.py:parse_token_usage() 从 LLM 响应提取 token
    → store.py:insert_usage() 写入 usage_records 表
```

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/billing/usage.py` | 从 OpenAI 兼容响应解析 `TokenUsage`（input/output/total） |
| `packages/billing/store.py` | Postgres `usage_records` 建表、插入、聚合、CSV 导出 |
| `packages/billing/budget.py` | 日/月汇总 vs 预算比较，返回 `BUDGET_EXCEEDED` |
| `packages/billing/recorder.py` | 编排：解析 → 落库，失败仅打日志 |
| `apps/gateway/request_guards.py` | `check_token_budget()` — 请求拦截钩子 |
| `apps/gateway/billing_routes.py` | 查询 API：`/internal/billing/usage\|export\|invoice` |

#### 设计要点

- **可选依赖**：`DATABASE_URL` 未配置时整个计费模块跳过，主路径不受影响
- **两种预算粒度**：`token_budget_daily`（自然日 UTC）和 `token_budget_monthly`（自然月），`-1` 表示不限
- **预拦截**：请求转发前查预算，超限直接 429，不浪费 LLM 调用
- **平台计量**：成功响应可选带 `_platform.usage` 字段，包含当次用量和剩余预算
- **CSV 导出**：仅 admin 可用，用于对账

#### 兜底设计

`record_upstream_usage()` 解析或落库失败时只打 `logger.exception`，不中断主请求。

---

### 7.2 B2 — 密钥托管 / 混合检索 / 可观测栈（#7/#8/#10）

见 [Phase B2 章节](#6-phase-b2并行交付)。

---

### 7.3 B3 — Rerank + KB 金丝雀（#9）

#### 目标

在 RAG 检索后增加重排序，提升关键文档排名；支持知识库版本的金丝雀发布，降低新版本上线风险。

#### 架构

```
/v1/rag/query
  → resolve_query_version()     ← 金丝雀分桶
    → retrieve()                ← 向量/hybrid 检索
      → rerank_chunks()         ← 检索后重排（stub 模式）
        → min_score 过滤
          → LLM 生成
```

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/rag/routing.py` | 金丝雀路由：确定性分桶、版本选择 |
| `packages/rag/rerank.py` | 重排序编排（stub/api/local 三种模式） |
| `apps/gateway/rag/pipeline.py` | 路由配置查询 API |

#### Rerank（rerank.py）

`rerank_chunks(query, chunks, top_n, mode)` 调用对应 provider 对检索候选重排序：

| mode | 行为 | 用途 |
|------|------|------|
| `stub` | 词面重合度排序，免 GPU | 开发验证 |
| `api` | 调外部 rerank 服务 | 生产 |
| `local` | 本地模型重排 | 离线实验 |

stub 模式下会把与 query 词面更相关的 chunk 排到前面，验证 rerank 环节的流程完整性。`timings` 增加 `rerank_ms`。

#### KB 金丝雀（routing.py）

```yaml
# config/rag.yaml
kb_routing:
  lab-demo:
    stable_version: 1
    canary_version: 2
    canary_percent: 30   # 30% 流量走 v2
```

`pick_query_version()` 核心逻辑：

1. 请求体带 `version` → 固定该版本（`pinned`）
2. 未配置路由或 `canary_percent=0` → 全量 `stable_version`
3. 计算 `sha256(tenant_id:query)` 取模 100 → 如果 < `canary_percent`，走 canary

```python
def routing_bucket(tenant_id: str, query: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}:{query}".encode()).hexdigest()
    return int(digest[:8], 16) % 100
```

同一租户同一次查询始终路由到同一版本，保证一致性。响应带 `_platform.routing` 说明路由结果。

#### 关键设计决策

| 决策 | 原因 |
|------|------|
| 金丝雀分桶用 hash 而非随机 | 同一用户+查询稳定路由，避免不一致 |
| Rerank 默认 stub 模式 | 零 GPU 依赖，可用于开发/CI 验证流程 |
| 回滚只需改 canary_percent=0 | 不改代码、不重索引，秒级回滚 |
| rerank 在 min_score 之前 | 重排后更相关的文档可能提升到阈值之上 |

---

## 8. Phase C：平台化（#11～#14）

Phase C 把「单租户网关」扩展为 **平台管理面**：多供应商、Region 驻留、租户自助、工具市场。

```
Phase C
  ├─ #11 供应商矩阵     — config/providers.yaml → registry → model_router 选 provider
  ├─ #12 Region 驻留    — config/regions.yaml → 请求级 X-Region → data_zone 校验
  ├─ #13 租户自助 API   — PATCH limits + GET profile → tenant_overrides.json 持久化
  └─ #14 工具市场       — tools_marketplace.yaml → 申请 → admin 审批 → 自动加入白名单
```

---

### 8.1 供应商矩阵（#11）

#### 目标

支持配置多个 LLM 供应商（OpenAI / Anthropic / Google / 本地），按策略（balanced / cost / latency）自动选最优 provider。

#### 代码链路

```
pick_provider_for_model(model_name)
  → get_provider_matrix()       ← 读 providers.yaml，构建 ProviderMatrix
  → 按 routing_policy 打分      ← _score(): cost 看价格，latency 看延迟，balanced 加权
  → 返分最高的 ModelOffering    ← 含 base_url + api_key + 单价

forward_with_model_router()
  → 调 pick_provider_for_model() ← 每个模型尝试时获取对应 provider
  → forward_chat_completions()    ← 用 provider 的 base_url + api_key 转发
  → 响应带 provider_id           ← _platform.provider_id
```

#### 设计要点

- **api_key_env 间接引用**：凭据 key 名写在 YAML，实际值从环境变量读取
- **兜底**：providers 中找不到候选模型时，自动用 `settings.llm_base_url` + `LLM_API_KEY`
- **打分策略**：`cost` = 负价格，`latency` = 负延迟，`balanced` = `-(price×10 + latency×0.01)`
- **可选依赖**：不配置 providers.yaml 时完全向后兼容

#### 关键文件

| 路径 | 职责 |
|------|------|
| `config/providers.yaml` | 供应商列表 + 路由策略 + 价目 |
| `packages/providers/registry.py` | 读 YAML、构建矩阵、`pick_provider_for_model()` 选最优 |
| `packages/router/model_router.py` | `forward_with_model_router()` 调 registry 选 provider |
| `apps/gateway/model_router.py` | 薄 re-export |

---

### 8.2 Region 驻留（#12）

#### 目标

支持多 Region 部署，Qdrant 就近路由；通过 data_zone 确保数据驻留合规。

#### 代码链路

```
请求到达 → resolve_region()
  1. 取 region：X-Region 请求头 → 租户 home_region → 配置文件 default_region
  2. 校验 region 是否存在 → 否: REGION_UNKNOWN
  3. 校验 data_zone 匹配 → 否: DATA_RESIDENCY_VIOLATION

→ set_request_region()    ← 写入 ContextVar
→ 后续 Qdrant 操作读 get_request_qdrant_url()
→ clear_request_region()  ← 请求结束清理
```

#### 设计要点

- **ContextVar 而非请求参数透传**：避免在函数签名中传递 region，降低耦合
- **三层 region 来源**：`X-Region` 请求头 → `tenants.yaml home_region` → `regions.yaml default_region`
- **data_zone 校验**：租户声明数据归属区域，与 region 的实际 data_zone 不匹配时返回 403

#### 关键文件

| 路径 | 职责 |
|------|------|
| `config/regions.yaml` | Region 定义 + data_zone 映射 |
| `packages/region/resolver.py` | `resolve_region()` 解析与校验 |
| `packages/region/context.py` | ContextVar 管理当前请求的 region/qdrant_url |

---

### 8.3 租户自助 API（#13）

#### 目标

租户/管理员通过 API 查询和修改租户配置，替代手动改 YAML。

#### 代码链路

```
GET /internal/tenants/{id}/profile
  → 返回：配额/限流/模型白名单/工具白名单/home_region/data_zone/kb_versions

PATCH /internal/tenants/{id}/limits
  → 修改：daily_request_quota, token_budget_daily/monthly, rate_limit, allowed_tools
  → 权限：仅 platform_admin
  → 持久化：data/tenant_overrides.json
  → 生效方式：merge_tenant_overrides() 将 JSON 覆盖合并到 YAML 静态配置
```

`allowed_tools` 是取并集（非替换），避免并行操作覆盖。

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/tenant_admin/overrides.py` | 读写 `tenant_overrides.json`、合并覆盖 |
| `apps/gateway/platform_routes.py` | `/internal/tenants/*` + `/internal/providers/*` + `/internal/tools/*` |
| `packages/auth/rbac.py` | 角色分层 viewer < developer < tenant_admin < platform_admin |

#### RBAC 角色体系

```python
ROLE_HIERARCHY = ("viewer", "developer", "tenant_admin", "platform_admin")

can_patch_tenant_limits → role >= platform_admin
can_approve_tools       → role >= platform_admin
can_view_tenant_profile → 自己可见 或 role >= platform_admin
```

---

### 8.4 工具市场（#14）

#### 流程

```
租户 → GET /internal/tools/marketplace          ← 浏览目录（含 risk 分级）
租户 → POST /internal/tools/requests             ← 提交申请
admin → POST /internal/tools/requests/{id}/approve
  → approve_tool_request()
    → patch_tenant_limits(tenant_id, allowed_tools=[...])  ← 自动加白名单
    → 下次 agent run 新工具可用
```

审批通过时自动将工具追加到租户 `allowed_tools` 白名单。配置在 `config/tools_marketplace.yaml`，每项工具含 risk 分级和是否需要审批标记。

#### 关键文件

| 路径 | 职责 |
|------|------|
| `config/tools_marketplace.yaml` | 工具目录 + risk 分级 |
| `packages/agent/marketplace.py` | 目录读取、申请创建/审批/拒绝 |
| `packages/tenant_admin/overrides.py` | `patch_tenant_limits()` 写工具白名单 |
| `apps/gateway/platform_routes.py` | `/internal/tools/*` 路由 |

---

## 9. Phase D：运维与治理（#15～#23）

Phase D 在 Phase C 的平台化基础之上做 **运维与治理**：熔断保护、JWT 鉴权、审计双写、控制台 MVP、Redis Session、金丝雀自动回滚、成本估算。五个波次并行。

```
Phase D
  ├─ D1 运维     — 熔断器 + Grafana 面板 + Prometheus 告警 + 多实例说明
  ├─ D2 治理     — JWT HS256 + RBAC + 审计 Postgres 双写
  ├─ D3 控制台   — apps/console React 管理台 + JSON API
  ├─ D4 效果     — Redis Session + 金丝雀自动回滚 + MCP stub
  └─ D5 商业化   — 成本估算 + 月度账单 /internal/billing/invoice
```

**原则**：观测与治理不侵入业务主路径——中间件/守卫层挂载，失败可降级。

---

### 9.1 D1 — 熔断 + Grafana（#15～#17）

#### 熔断器（circuit_breaker.py）

按 key（如 model 名）维护熔断状态，三种状态：closed → open → half_open → closed。

```python
breaker.allow("gpt-4o")       # → (True, "closed")
breaker.record_failure("gpt-4o")  # 连续 5 次 → open
breaker.allow("gpt-4o")       # → (False, "open")  → 503 CIRCUIT_OPEN
# 30 秒后自动 half_open，下一个请求成功则 closed
```

#### 集成链路

```
forward_with_model_router()
  → 每个模型尝试前: breaker.allow(model_name)
    → not allowed → 返回 503 熔断错误，不转发
  → 成功: breaker.record_success(model_name)
  → 失败: breaker.record_failure(model_name)
    → 是否 fallback 到链中下一个模型
```

#### Grafana + Prometheus 告警

- `config/grafana/dashboards/gateway-overview.json` — QPS、延迟 P95、错误率、熔断状态
- `config/prometheus/alerts.yml` — 高错误率、高延迟、熔断触发告警规则
- `docker compose --profile observability up -d` 启动（与 Phase B2 共享）
- 多 gateway 实例共享 Redis 配额：`docker compose up -d --scale gateway=2`

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/router/circuit_breaker.py` | 按 key 熔断计数 + 三种状态机 |
| `packages/router/model_router.py` | 在转发前/后调 breaker |
| `config/grafana/dashboards/gateway-overview.json` | Grafana 面板定义 |
| `config/prometheus/alerts.yml` | 告警规则 |

---

### 9.2 D2 — JWT + RBAC + 审计双写（#18～#19）

#### JWT 鉴权（jwt_hs256.py）

可选替代 Bearer Token：启用后 gateway 先解析 JWT 提取 `tenant_id` + `role`，再校验。

```python
def decode_hs256(token: str, secret: str) -> dict[str, Any] | None:
    # 最小 HS256 实现：base64url 解码 header + payload
    # hmac.compare_digest() 校验签名
    # 返回 payload 字典或 None
```

启用方式：

```bash
export AUTH_JWT_ENABLED=true
export AUTH_JWT_SECRET=your-dev-secret
```

#### RBAC 角色分层

```python
ROLE_HIERARCHY = ("viewer", "developer", "tenant_admin", "platform_admin")
```

| 权限 | 最低角色 |
|------|---------|
| 查看自己租户画像 | 任意 |
| PATCH 租户限额 | platform_admin |
| 审批工具市场申请 | platform_admin |
| 查看所有租户 | platform_admin |

#### 审计 Postgres 双写（postgres_store.py）

在 SQLite 审计（Phase A）基础上，`DATABASE_URL` 可达时同步写 Postgres `audit_events` 表：

```
请求 → TraceIdMiddleware → resolve_tenant → 业务路由 → 响应
                                                     ↓
                                          AuditPostgresStore.insert()
```

每条记录：tenant_id, actor_role, method, path, status_code, latency_ms, trace_id, model, error_code。

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/auth/jwt_hs256.py` | 最小 HS256 JWT 解析，无外部依赖 |
| `packages/auth/rbac.py` | 角色 hierarchy + 权限检查函数 |
| `packages/audit/postgres_store.py` | Postgres `audit_events` 建表 + 插入 + 查询 |

---

### 9.3 D3 — 控制台 MVP（#20）

#### 目标

提供浏览器可访问的管理界面，替代手动 curl 调用 internal API。

#### 架构

```
浏览器 → GET /console/  → 返回 apps/console/index.html（静态入口）
         → 加载 JS/CSS  → React SPA
         → 调 /internal/* API → 数据展示
```

Console V2 是 React SPA，构建产物在 `apps/console/static/`，入口 `apps/console/index.html` 通过 FastAPI `StaticFiles` 挂载到 `/console/` 路径。

**Console JSON API**（`console_routes.py`）：

| 端点 | 用途 |
|------|------|
| `POST /internal/auth/token` | Console 登录（校验 tenant_id + api_key） |
| `GET /internal/tenants` | 租户列表（含本月用量） |
| `GET /internal/metrics` | Dashboard 数据（QPS、错误率、token 用量） |
| `GET /internal/settings` | 平台全局配置 |
| `GET /internal/rag/knowledge-bases` | KB 管理（查询） |
| `POST /internal/rag/knowledge-bases` | 创建 KB |
| `DELETE /internal/rag/knowledge-bases/{id}` | 删除 KB |
| `GET /internal/rag/knowledge-bases/{id}/documents` | 文档列表 |
| `POST /internal/rag/knowledge-bases/{id}/documents` | 上传文档 |
| `DELETE /internal/rag/knowledge-bases/{id}/documents/{doc_id}` | 删除文档 |
| `POST /internal/rag/query` | RAG 检索调试 |

#### 关键文件

| 路径 | 职责 |
|------|------|
| `apps/console/index.html` | Console 入口 HTML |
| `apps/console/static/` | React SPA 构建产物 |
| `apps/gateway/console_routes.py` | Console 后端 JSON API |

---

### 9.4 D4 — Redis Session + 金丝雀守卫 + MCP stub（#21～#22）

#### Redis Session（session_redis.py）

第 4 周的 Agent Session 在内存中，多 gateway 实例间不共享。启用 Redis 后：

```python
class RedisSessionStore:
    def save_session_state(self, tenant_id, session_id, state):
        # Redis SETEX TTL=86400s（24 小时）
    def get_session_state(self, tenant_id, session_id):
        # Redis GET → parse_session_raw()
```

`REDIS_URL` 未配置时自动回退内存 SessionStore。

#### 金丝雀自动回滚（canary_guard.py）

Phase B3 的金丝雀需要管理员手动调 `canary_percent`。D4 让它**自动回滚**：

```
python packages/rag/canary_guard.py check --kb-id lab-demo --min-pass-rate 0.85
```

流程：

```
1. 扫描 eval/runs/ 目录，取最新的 eval 报告
2. 读 pass_rate
3. pass_rate >= min_pass_rate → noop
4. pass_rate < min_pass_rate → 写 data/canary_guard.json
   → kb_routing_overrides.{kb_id}.canary_percent = 0
   → 记录回滚时间 + 原因
   → 发送 webhook 通知
   → 记录 canary_auto_rollback metric
```

`canary_guard.json` 被 routing 层读取，自动覆盖 `rag.yaml` 中的 `canary_percent`。**不改代码、不重索引**。

#### MCP stub

`config/mcp_tools.json` 定义 MCP 工具描述，通过 `mcp_echo` 工具验证 MCP 协议集成流程。

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/agent/session_redis.py` | Redis 后端 SessionStore |
| `packages/rag/canary_guard.py` | eval pass_rate 阈值检测 + 自动回滚 |
| `config/mcp_tools.json` | MCP 工具定义 stub |

---

### 9.5 D5 — 成本估算（#23）

#### 目标

根据用量和供应商单价估算月度成本。

#### 实现

```python
def estimate_cost_usd(*, model, input_tokens, output_tokens):
    matrix = get_provider_matrix()
    for offering in matrix.offerings:
        if offering.model == model:
            return (input_tokens/1000)*input_price + (output_tokens/1000)*output_price
    return 0.0
```

单价来自 `config/providers.yaml` 中每个模型定义的 `input_price_per_1k` 和 `output_price_per_1k`。

API：`GET /internal/billing/invoice?month=2026-05` 返回自然月汇总 + 每条用量估算成本 + 总计。

#### 关键文件

| 路径 | 职责 |
|------|------|
| `packages/billing/cost.py` | `estimate_cost_usd()` 按模型和 token 估算 |
| `apps/gateway/billing_routes.py` | `/internal/billing/invoice` 端点 |

---

### 设计理念

| 维度 | 做法 | 原因 |
|------|------|------|
| 熔断 | 按模型名独立计数，自动 half-open 恢复 | 避免单一模型故障影响其他模型 |
| JWT | 最小实现，无外部依赖 | 可选增强，不增加部署复杂度 |
| 审计 | SQLite + Postgres 双写 | 开发阶段 SQLite 零配置，生产切到 Postgres |
| Console | 静态 SPA + JSON API | 前后端分离，后端无状态 |
| Session | Redis 优先，内存保底 | 多实例共享，单实例零配置 |
| 金丝雀守卫 | eval pass_rate 驱动自动回滚 | 数据驱动，无需人工判断 |
| 成本 | 按 providers.yaml 单价估算 | 与供应商矩阵配置同源，一致性好 |

---

## 9. Phase E：Agent 效果深化（#24～#28）

Phase E 在 Phase D Agent 治理基础上，从"能跑"升级到"跑得准、跑得省、跑得可控"——轨迹评测、工具路由、上下文预算、质量门与反思、Human-in-the-Loop。

```
Phase E
  ├─ E1 轨迹评测     — 五维度指标：选对工具、不调禁用、参数正确、第一步准、总体通过
  ├─ E2 意图路由     — 关键词匹配 query → 缩小候选工具集；Tool-RAG 词袋打分
  ├─ E3 上下文预算   — tool 结果截断 + 保留最近 N 轮 + 滚动摘要 + 超 budget 删最旧
  ├─ E4 质量门 + 反思 — ToolEnvelope quality_score 低分 → 注入 platform_quality_hint
  └─ E5 HITL + Shadow— 高风险工具 202 暂停审批；X-Agent-Shadow 只记录不执行
```

**原则**：全部在 runner 主循环挂载，每项可独立开关。

### 9.6 E1 — 轨迹评测（#24）

**五维度指标**：

| 指标 | 含义 |
|------|------|
| `tool_precision_at_1` | 第一步选对工具比例 |
| `needless_tool_rate` | 误调禁用工具比例 |
| `missing_tool_rate` | 漏调工具比例 |
| `arg_valid_rate` | 参数校验成功率 |
| `pass_rate` | 业务断言通过率 |

用例文件 `eval/agent_baseline.jsonl`，5 条覆盖 calc/KB/chitchat/forbidden/白名单。`validate-baseline` 无需 LLM Key，CI 中可离线校验。

### 9.7 E2 — 意图路由 + Tool-RAG（#25）

`tool_router.py` 根据 query 从 YAML 配置的 intents 中做关键词匹配，缩小暴露给 LLM 的工具列表。支持三种策略：`intent`（关键词）、`rag`（词袋余弦重叠）、`both`。白名单始终生效。

### 9.8 E3 — 上下文预算 + 滚动摘要（#26）

`assemble_llm_messages()` 四步：tool 结果截断 → 保留最近 N 轮 → 拼 `[session_summary]` 前缀 → 超 budget 删最旧。每 6 轮触发 `maybe_compact_session()` 滚动摘要。

### 9.9 E4 — 质量门 + 反思（#27）

`assess_tool_output()` 解析 `ToolEnvelope` 中的 `quality_score`，KB 空结果或低分时注入 `platform_quality_hint`。`reflect_max_retries=2` 限制反思轮数。

### 9.10 E5 — HITL + Shadow（#28）

- **HITL**：`risk_level=high` 工具拦截 → `202 pending_approval` → 管理员 `POST confirm` → client resume
- **Shadow**：`X-Agent-Shadow: true` → 全量记录不执行 → `shadow_tool_calls`
- 审批持久化到 `data/agent_approvals.json`

---

## 10. Phase F：能力中台补全（#29～#33）

Phase F 在 Agent 效果深化之后，把 Prompt 版本化、A/B 实验、长记忆、MCP 集成、上下文压缩这些能力从"手写可用"变成"平台原生支持"。

```
Phase F
  ├─ #29 Prompt 版本化   — PromptVersion draft→active→archived，YAML+JSON 双存储
  ├─ #30 A/B 实验        — SHA256 确定性分桶，自动胜出，Promote 上线
  ├─ #31 长记忆          — session/user/tenant 三级作用域，Postgres/内存双后端
  ├─ #32 MCP 集成        — stdio+http 双 transport，JSON-RPC 2.0 协议桥接
  └─ #33 上下文压缩      — L1 滑窗 + L2 LLM 摘要 + L3 Token 感知注入
```

---

### 10.1 #29 — Prompt 版本化

**问题**：之前 Prompt 是零散的 `.txt` 文件，没有版本管理。运营改了 prompt 后无法回滚，不同租户也无法使用不同 prompt。

**方案**：`PromptRegistry` 管理 `PromptVersion`（draft → active → archived 状态机），双存储层：YAML 基线（config/prompts.yaml，git 跟踪） + JSON overrides（data/prompt_overrides.json，运行时管理 API 写入）。启动时 YAML 先加载，JSON 覆盖同 (tenant_id, prompt_id, version) 条目。

**模板语法** `{{var}}`：双花括号而非 `str.format()` 的 `{var}`。原因：RAG 模板已有 `{context}/{query}` 占位符，`str.format()` 会冲突。未提供变量时保持占位符原样，方便开发期定位问题。

**向后兼容**：`_legacy_fallback_get()` 在 registry 找不到 prompt_id 时回退到 legacy `.txt` 文件（version=0 标记），保证零迁移成本。

**关键方法**：

| 方法 | 用途 |
|------|------|
| `get_active()` | 取 active 版本；无 active 则取最新非 draft |
| `create_version()` | 新版本，旧 active → archived |
| `render_with_experiment()` | A/B 分桶渲染（#30 集成点） |
| `_persist()` | 全量写入 JSON overrides |

**线程安全**：所有读写用 `threading.RLock` 保护。全局单例模式 `init_registry()/get_prompt_registry()`。

---

### 10.2 #30 — Prompt A/B 实验

**问题**：运营想试验哪个 prompt 版本效果更好，但没有实验框架。

**方案**：`ExperimentStore` 管理 `Experiment` + `ExperimentVariant(version, percent)`。

**确定性分桶**：

```
hash = SHA256(experiment_id + bucket_key)
bucket = int(hash[:8], 16) % 100
→ 按 variant.percent 累加边界 → 返回命中的 variant
```

同一 `bucket_key`（如 session_id）始终分到同一版本，用户体验一致。

**自动胜出**：`maybe_auto_winner()` 检查所有 variant requests >= min_samples → 按 quality/latency/tokens 算分 → 第一名与第二名相对差距 >= winner_margin → 标记 winner 并停止实验。**不自动 set_active**，需 admin 显式 `promote_winner()`。

**指标收集**：

| 指标 | 记录时机 |
|------|---------|
| `latencies_ms`（上限 500 条） | 每次 `record_request()` |
| `quality_scores` | 每次 `record_quality()` |
| `tokens_used`, `errors` | 每次 `record_request()` |

---

### 10.3 #31 — 长记忆

**问题**：Agent 对话只在 session 内有 messages，session 结束后历史丢失。用户下次来需要重复上下文。

**方案**：三级作用域 + 双后端 + 自动摘要。

**MemoryRecord**：tenant_id（租户隔离）+ scope（session/user/tenant）+ scope_id + content + metadata（turn_count、role 等）。

| 后端 | 条件 |
|------|------|
| `PostgresMemoryStore` | `DATABASE_URL` 已配置，JSONB 存 metadata |
| `InMemoryMemoryStore` | 无 DATABASE_URL，dict 内存降级 |

**搜索**：单词重叠率（关键词模式）+ 余弦相似度（语义模式占位），按 score 降序取 top_k。

**自动摘要**：每 `MEMORY_SUMMARIZE_EVERY_N_TURNS`（默认 8）轮触发 LLM 压缩对话 → 保存摘要到 memory store。

**Postgres 表**：`agent_memories(id UUID, tenant_id, scope, scope_id, content, metadata JSONB, created_at)`，索引 `(tenant_id, scope, scope_id)`。

---

### 10.4 #32 — MCP 集成

**问题**：Agent 只能调用内置工具（calc、get_kb_snippet），无法接入外部 MCP 服务器提供的工具。

**方案**：`MCPServerRegistry`（配置管理 + 健康状态）+ `MCPClient`（JSON-RPC 2.0 协议实现）。

**协议流程**：

```
connect() → initialize → notifications/initialized → ready
  → tools/list → MCPTool[]
  → tools/call(name, args) → result
```

**Transport**：

| 类型 | 实现 |
|------|------|
| stdio | `asyncio.create_subprocess_exec(command)` 子进程 stdin/stdout |
| http | `httpx.AsyncClient(base_url)` POST /json-rpc |

**工具桥接**：MCP 工具转为 `ToolDefinition`，命名 `mcp_{server_id}_{tool_name}`，注册到 Agent 工具市场。

**故障隔离**：单 MCP 服务器失败不影响其他服务器；启动时连接失败仅标记 unhealthy，不阻塞 gateway。

---

### 10.5 #33 — 上下文压缩

**问题**：Phase E 的 `stub_summarize` 只是字符串拼接，质量差；长记忆注入没有 token 感知。

**方案**：三层压缩策略 + 完整降级链。

| 层 | 策略 | 降级 |
|----|------|------|
| L1 | 滑窗截断 `drop_oldest_until_budget()` | 始终启用 |
| L2 | `maybe_compact_with_llm()` 调 LLM 摘要 | LLM 失败 → stub → 跳过 |
| L3 | `retrieve_and_inject_memory()` 动态裁剪 | MemoryStore 不可用 → 跳过 |

**`llm_summarize()`**：复用 `packages.memory.summarize.summarize_messages()`，失败回退 `stub_summarize()`。

**Token 感知注入**：`retrieve_and_inject_memory()` 检查 `budget_remaining >= 200`，逐条估算 token 动态裁剪以适应 budget。

**元数据**：`_platform.compress`（summary_source/compressed_messages）+ `_platform.memory`（injected/memory_count/injected_tokens）。

---

### 10.6 Phase F 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| 模板语法 `{{var}}` | 双花括号正则替换 | 避免与 RAG 模板 `{context}` 冲突 |
| Prompt 双存储层 | YAML + JSON overrides | 基线 git 跟踪，运行时覆盖分离 |
| A/B 分桶 SHA256 | 确定性哈希 | 同一 session 始终同版本 |
| 自动胜出不自动上线 | 仅标记 winner | 给 admin 审查机会 |
| 记忆三级作用域 | session/user/tenant | 灵活控制可见范围 |
| Postgres 记忆存储 | JSONB + 应用层搜索 | 无需向量库，降级为关键词 |
| MCP 双 transport | stdio + http | 本地子进程 + 远程服务器 |
| LLM 摘要降级链 | LLM → stub → 跳过 | 不阻塞 Agent 主循环 |

---

## 11. Phase G：模型服务增强（#34～#35）

Phase G 在能力中台补全之后，做 **模型服务增强**：语义缓存降本、Embedding 独立服务化。

```
Phase G
  ├─ #34 语义缓存         — Gateway 层拦截 /v1/chat/completions，exact/semantic 双模式
  └─ #35 Embedding 独立服务 — Provider 抽象 + Registry + LRU 缓存 + REST API
```

---

### 11.1 #34 — 语义缓存

**问题**：用户反复问相同或近义问题（如"你好"→"你好呀"），每次都要调 LLM，浪费 token 和成本。

**方案**：在 Gateway 的 `/v1/chat/completions` 路径上加缓存，quota 检查之后、上游调用之前查。

**双模式命中策略**：

| 模式 | 策略 | 适用场景 |
|------|------|---------|
| `exact` | SHA256(tenant_id + model + normalized_messages) | 零依赖高一致性 |
| `semantic` | Embedding 余弦相似度 >= 0.92 | 容忍近义复述，降本更多 |

semantic 模式 embedding 不可用时自动降级 exact。

**跳过条件**：`stream=true` / `temperature > 0.3` / 模型在黑名单中。

**双后端**：InMemory（进程内 LRU + TTL）/ Redis（Hash + TTL，跨实例共享）。按 tenant_id 分桶隔离。

**可观测**：`semantic_cache_hits_total` / `misses_total` / `tokens_saved_total` / `lookup_latency_ms_p95` Prometheus 指标。

### 11.2 #35 — Embedding 独立服务

**问题**：之前 Embedding 内联在 RAG pipeline 里，换模型、加缓存、限流都得改 RAG 代码。Memory 搜索、语义缓存等新功能也需要 embedding，没法复用。

**方案**：三个核心抽象：

1. **EmbeddingProvider**：StubProvider（MD5 确定性哈希）+ OpenAIProvider（调 OpenAI API）+ provider_factory 自动降级
2. **EmbeddingRegistry**：YAML + JSON overrides 双层注册表（与 Prompt/MCP 同模式）
3. **EmbeddingService**：统一 `embed()` 接口 + LRU 缓存（OrderedDict，maxsize=10000）+ 批量混合 hit/miss

**Provider 降级链**：`openai` 无 API Key → StubProvider；未知 provider → StubProvider。

**REST API**：7 个端点，`/internal/embeddings/*`，模型 CRUD + embed + 缓存管理。

**种子模型**：`config/embedding_models.yaml` 含 Qwen3-Embedding-8B（4096 维，内网默认）、text-embedding-3-small、stub-embedding、stub-multimodal。

### 11.3 Phase G 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| 缓存位置 | quota 之后、上游之前 | 配额拦截后不浪费缓存查询 |
| Redis 语义匹配 O(N) | 遍历 Hash | 中小流量够用 |
| Embedding Provider 工厂 | provider + env 自动决策 | 无 Key 自动降级 Stub，CI 零依赖 |
| LRU 用 OrderedDict | 进程内 O(1) | 无需额外依赖 |
| 批量混合 hit/miss | 只调 provider 算 miss | 减少 token 消耗 |

---

## 12. Phase H：Agent 高阶能力（#37～#40）

Phase H 在模型服务增强之后，做 **Agent 高阶能力**：控制流编排、Multi-Agent 协作、Agent 生命周期管理、HITL 完整版。四个能力让 Agent 从"单轮工具调用"升级到"可编排、可协作、可版本管理、有人审批"的**生产级 Agent 平台**。

```
Phase H
  ├─ #37 控制流编排       — DAG 工作流引擎，10 种节点类型，模板引用 ${node_id.field}
  ├─ #38 Multi-Agent     — 4 种角色 primary/specialist/reviewer/router，三重防递归
  ├─ #39 Agent 生命周期   — draft→active→archived 状态机，三种发布策略
  └─ #40 HITL 完整版     — InMemory/SQLite 双后端，Webhook HMAC 签名，超时扫描
```

---

### 12.1 #37 — 控制流编排

**问题**：Agent 只能线性 ReAct 循环，无法表达复杂工作流——先检索、再分类、然后根据结果走不同分支。

**方案**：DAG 工作流引擎，10 种节点类型。

| 节点类型 | 功能 |
|---------|------|
| `start` / `end` | 入口/出口 |
| `llm_call` | 调用 LLM 并获取回复 |
| `tool_call` | 调用 Agent 工具 |
| `condition` | 条件分支，按表达式选择 target |
| `parallel` | 并行 fan-out + gather |
| `loop` | 循环执行子图 |
| `output` | 输出节点（支持模板引用） |
| `agent_call` | 委托子 Agent（#38 集成点） |
| `plan_step` | Plan 阶段步骤 |

**执行模型**：
1. 从 `start_node` 开始拓扑遍历
2. 执行当前节点，输出写入 `ExecutionContext.outputs`
3. 评估出边条件 → 选择下一节点
4. `condition` 节点直接跳转 `branch.target`（非边匹配）
5. 到达 `end_node` 返回 `ExecutionResult`

**模板语法** `${node_id.field}`：节点输出可被后续节点引用。`evaluate_condition()` 用 `eval()` + 词法过滤（禁止 `import/exec/eval/open/__/lambda`）+ 限制命名空间。

**安全限制**：最大节点执行数 100 / 总超时 300s / 并行最大分支 5。

**节点执行器注册表** `register_node_executor(name, fn)`：全局 `dict[str, Callable]`，新增节点类型不侵入 engine。

---

### 12.2 #38 — Multi-Agent 协作

**问题**：单 Agent 上下文窗口有限，无法专业化分工。复杂任务需要 RAG 专家 + 代码审核 + 翻译等多个 Agent 协作。

**方案**：`AgentRegistry` + `delegate_to_agent()` 双组件架构。

**4 种 Agent 角色**：

| 角色 | 说明 | 典型场景 |
|------|------|---------|
| `primary` | 主 Agent，与用户直接交互 | 入口 Agent，协调其他子 Agent |
| `specialist` | 专家 Agent | RAG 检索、代码生成、翻译 |
| `reviewer` | 审核 Agent | 监督输出质量 |
| `router` | 路由 Agent | 分析意图并分发任务 |

**`AgentSpec` 核心字段**：role / system_prompt / allowed_tools / model / `can_delegate` / `can_be_delegated_to` / `max_delegation_depth`。

**三重防递归机制**：
1. **委托栈**：每次委托记录栈，检测循环（A→B→A 拒绝）
2. **最大深度**：`max_delegation_depth`（默认 3）
3. **双向标志**：`can_delegate` + `can_be_delegated_to` 共同控制

**三种协作模式**：
1. **委托**：主 Agent → `delegate_to(agent_id, task)` → 子 Agent 执行 → 返回结果
2. **并行委托**：`parallel_delegate([{agent_id, task}, ...])` → `asyncio.gather` 并发执行
3. **链式**：编排引擎中 `agent_call` 节点串联多个 Agent

**`resolve_delegation_tools()`**：AgentSpec 白名单与租户 ACL 取交集，确保工具权限不出圈。

---

### 12.3 #39 — Agent 生命周期管理

**问题**：Agent 配置变更直接生效，出问题无法回退；无法灰度验证新版效果。

**方案**：`AgentLifecycleRegistry` 管理 `AgentVersion`（draft → active → archived 状态机）。

**版本状态机**：
```
注册 → [draft] → activate → [active] ←──── rollback
                                │
                   新版本激活 ↓
                           [archived]
```

**三种发布策略**：

| 策略 | 初始流量分配 | 适用场景 |
|------|-------------|---------|
| `all_at_once` | `{new: 100}` | 小改动全量上线 |
| `blue_green` | `{old: 50, new: 50}` | 大版本升级，快速回滚 |
| `canary` | `{old: 90, new: 10}` | 灰度验证，风险可控 |

**关键方法**：`register_version()` 自增版本号 → `activate_version(strategy)` 切换 active + 设 traffic_split → `rollback_version()` active ← previous。

**持久化**：`config/agent_versions.yaml` + `data/agent_versions_overrides.json`（与 Prompt/MCP 同模式）。

> **注意**：`traffic_split` 目前为元数据存储，实际流量路由需在调用层读取并按概率分配，本模块不实现路由逻辑。

---

### 12.4 #40 — HITL 完整版

**问题**：Phase E 的 HITL 是 `data/agent_approvals.json` 文件存储 stub，缺少持久化、Webhook、超时处理。

**方案**：完整审批工作流，双存储引擎 + Webhook 通知 + 超时扫描。

**状态机**：`pending → approved | rejected | timeout | cancelled`

| 引擎 | 触发条件 |
|------|---------|
| `InMemoryApprovalStore` | 默认/测试 |
| `SqliteApprovalStore` | `HITL_STORE_DATABASE_URL=sqlite:///...` |

**Webhook**：HMAC-SHA256 签名，指数退避重试（1s/2s/4s），3 次失败后放弃。

**超时扫描**：`timeout_expired_requests()` 扫描所有 `expires_at < now()` 的 PENDING 请求标记为 timeout。

**向后兼容**：`packages/agent/hitl.py` 通过委托 `packages.hitl` 实现，`HITL_ENABLED=false` 时回退 JSON 文件。

---

### 12.5 Phase H 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| 工作流 DAG vs 线性 | DAG 拓扑遍历 | 支持分支/并行/循环，表达能力强 |
| 节点执行器注册表 | `dict[str, Callable]` 全局注册 | 可扩展，新增节点类型不侵入 engine |
| 条件 eval 用 `eval()` | 沙箱 + 词法过滤 + 限制命名空间 | 轻量，生产应换 AST |
| Agent 角色 4 种 | primary/specialist/reviewer/router | 覆盖主从协作到监督审核全场景 |
| 防递归三重保护 | 栈 + 深度 + 标志 | 防止递归爆炸，层层设防 |
| 版本状态机 | draft → active → archived | 清晰的生命周期，支持回滚 |
| 生命周期持久化 | YAML + JSON overrides | 与 Prompt/MCP 统一模式 |
| HITL 双存储引擎 | InMemory + SQLite | 开发零配置，生产可持久化 |
| Webhook HMAC-SHA256 | 共享密钥签名 | 与 GitHub/Stripe Webhook 一致 |
| HITL 向后兼容 | 委托 packages.hitl | Phase E 的 JSON stub 仍可用 |

---

## 13. Phase I：安全与合规（#41～#44）

Phase I 在 Agent 高阶能力之后，做 **安全与合规**：沙箱容器隔离、动作分级审计、PII 脱敏、OAuth2/mTLS。四个能力从"执行隔离 → 操作审计 → 数据保护 → 传输认证"四个层面构建纵深防御体系。Sandbox/PII/Audit 主链路以 `/internal/*` API 就绪为主，Agent runner 尚未全量自动挂载。

```
Phase I
  ├─ #41 沙箱容器隔离     — Docker seccomp + gVisor 三 runtime，seccomp 四种档案
  ├─ #42 动作分级审计     — read/write/destructive/network/unknown 五级分类 + 启发式 fallback
  ├─ #43 PII 脱敏         — 正则引擎 + 7 种内置模式 + 4 种脱敏动作 + 内容安全关键词
  └─ #44 OAuth2/mTLS      — 授权码 + Client Credentials 双模式，mTLS 证书校验
```

---

### 13.1 #41 — 沙箱容器隔离

**问题**：Agent 工具调用（如 execute_code）可能包含恶意命令，LLM 生成的工具参数存在注入风险，直接子进程执行可能危害宿主机。

**方案**：三运行时 + seccomp 配置档案 + 可自定义 SandboxProfile，将危险工具封装在隔离层执行。

**三种运行时模式**：

| 运行时 | 隔离级别 | 适用场景 |
|--------|---------|---------|
| `process` | 无（直接子进程） | 开发/测试回退 |
| `docker` | 容器 + seccomp + `--cap-drop=ALL` + `--read-only` + `--network=none` | 生产首选 |
| `gvisor` | 容器 + seccomp + 用户态内核拦截 | 高安全场景 |

**seccomp 四种预定义档案**：

| profile_id | defaultAction | 允许的 syscall |
|-----------|--------------|---------------|
| `strict` | `SCMP_ACT_ERRNO` | 仅 read/write/exit/brk/mmap 等极少调用 |
| `default` | `SCMP_ACT_ALLOW` | 拒绝 mount/reboot/chroot/ptrace |
| `networking` | `SCMP_ACT_ALLOW` | 拒绝 bind/listen，允许 socket/connect |
| `readonly` | `SCMP_ACT_ALLOW` | 拒绝所有写系统调用 |

**SandboxProfile 自定义档案**：YAML + JSON overrides 定义（与 Prompt/MCP 同模式），支持 seccomp 规则、capabilities、只读/可写路径、网络开关。

**工具包装器集成点**：`tool_wrapper.py` 供 Agent Registry 标注 `requires_sandbox=True` 的工具路由到沙箱执行。

**安全威胁模型覆盖**：工具注入攻击、文件系统越权、网络横向渗透、特权提升、资源耗尽。

---

### 13.2 #42 — 动作分级审计

**问题**：工具调用的副作用差异巨大（`get_user` 无风险，`drop_table` 不可逆），没有分级就无法精准实施安全策略。

**方案**：ActionClassifier（注册表 + 启发式 fallback）+ ActionAuditLogger（内存审计日志）。

**五级分类模型**：

| 级别 | 说明 | 示例工具 |
|------|------|---------|
| `read_only` | 仅读取，无副作用 | calc, get_kb_snippet, list_users |
| `write` | 写入/修改，可逆 | create_record, update_profile |
| `destructive` | 不可逆高危操作 | delete_file, drop_table |
| `network` | 出站网络请求 | search_web_stub, httpbin_delay |
| `unknown` | 未知/未分类 | 新工具、未注册工具 |

**分类策略**：注册表优先级高于启发式。`get_classifier()` 可用时读注册表；无匹配时 fallback 到关键字匹配（工具名小写包含 delete/drop/rm → destructive，http/request/webhook → network 等）。

**与 HITL 联动**：`requires_approval()` 在 `action_level == destructive` 或 `requires_approval=True` 时返回 True。调用层（orchestrator/agent）负责检查并创建 HITL 请求，审计记录中 `approval_id` 指向对应的 HITL 审批。分类与流程控制关注点分离。

**审计日志**：ActionAuditEntry（entry_id, tenant_id, session_id, tool_name, action_level, arguments, result_summary, status, approval_id）。REST API `/internal/audit-actions/actions` 支持按 tenant/action_level 过滤。

---

### 13.3 #43 — PII 脱敏 + 内容安全

**问题**：用户消息中可能包含邮件、手机、身份证等 PII，未经脱敏直接发送给 LLM 提供商存在合规风险（GDPR/CCPA）。

**方案**：PIIDetector（正则引擎）+ Redactor（四种脱敏动作）+ ContentSafetyChecker（关键词安全），三层串联为完整流水线。

**7 种内置 PII 模式**：email（RFC 5322 简化）、phone_us（美国手机号）、ssn（XXX-XX-XXXX）、credit_card（Visa/MC/Amex/Discover）、ipv4、cn_id_card（中国居民身份证 18 位）、cn_phone（中国手机号）。

**四种脱敏动作**：

| 动作 | 效果 | 示例 |
|------|------|------|
| `redact` | 替换为模板 | `john@example.com` → `[REDACTED_EMAIL]` |
| `mask` | 保留首尾 N 位，中间 `*` | `john@example.com` → `jo**@ex*****.com` |
| `hash` | SHA256[:8] | `john@example.com` → `a3f7b2c1` |
| `block` | 含任意 PII 时返回空 | 整段文本 → `""` |

**内容安全**：四类关键词检测（hate/violence/sexual/self_harm），支持运行时注册新关键词。当前为关键词规则实现，接口签名兼容 LLM Moderation API 升级（如 OpenAI Moderation API）。

**完整流水线** `POST /internal/pii/process`：detect → redact → safety check，单次调用完成全部三阶段。

---

### 13.4 #44 — OAuth2 / mTLS

**问题**：现有 JWT HS256 鉴权在单点登录、第三方集成、服务网格等场景下不够，需要企业级认证机制。

**方案**：100% 向后兼容、opt-in 方式引入 OAuth2 授权码 + Client Credentials 双模式和 mTLS 客户端证书校验。

**OAuth2 流程**：
- Authorization Code Flow：浏览器 → `authorize` 重定向 IdP → 用户登录授权 → `callback` 换 token → access_token
- Client Credentials Flow：服务直接调 token endpoint → access_token（M2M 场景）

**mTLS 握手**：Client Hello → Server Certificate Request → Client Certificate → 通过 CA bundle 验证 + 提取 CN 作为 tenant_id。

**JWT Fallback**：`OAUTH2_JWT_FALLBACK=true`（默认）时 OAuth2 验证失败自动回退到 JWT HS256，实现渐进迁移。

**中间件分层**：Request → OAuth2Middleware（校验 Bearer token）→ mTLSAuthDependency（校验客户端证书）→ Route Handler。两者独立开关，零修改现有鉴权代码。

---

### 13.5 Phase I 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| 沙箱三运行时 | process/docker/gvisor | 开发回退到生产到高安全，渐进选择 |
| seccomp 档案预定义 | 4 种 + 自定义 | 覆盖通用/严格/网络/只读四场景 |
| 动作分级用注册表 + 启发式 | 双策略互补 | 注册表精确控制，启发式保证基线安全 |
| 审计日志用内存存储 | InMemory + database_url 预留 | 当前够用，未来可扩展 SQLite |
| PII 用正则而非 NER | 正则 μs 级 vs NER 数十 ms | 结构化 PII 正则精度足够 |
| PII block 动作 | 含 PII 直接阻断 | 合规场景（HIPAA）比脱敏更安全 |
| OAuth2 opt-in | OAUTH2_ENABLED=false 默认 | 不影响现有 JWT 鉴权 |
| mTLS opt-in | MTLS_ENABLED=false 默认 | 100% 向后兼容 |

---

## 14. Phase J：平台开发者体验（#45～#48）

Phase J 在安全与合规之后，做 **平台开发者体验**：Python SDK、Console V2 管理界面、评测 Pipeline + CI 门禁、在线反馈飞轮。前两个是面向外部开发者和管理员的平台界面，后两个是平台自身的质量闭环。

```
Phase J
  ├─ #45 Python SDK           — ai_platform_lab Client/AsyncClient，6 种资源，OpenAI 风格接口
  ├─ #46 Console V2           — React + Vite + Ant Design，9 个管理页面，JWT 认证
  ├─ #47 评测 Pipeline 门禁   — ≥200 条基线，类别 pass_rate 回退 >5% 则 block PR
  └─ #48 反馈飞轮             — 用户反馈 → quality monitor → LLM 建议 → A/B 实验闭环
```

---

### 14.1 #45 — Python SDK

**问题**：外部开发者想接入平台能力（Chat、RAG、Agent、Embedding、Memory、Orchestrator），但 Gateway REST API 需要手拼 HTTP 请求、处理鉴权、解析错误。没有 SDK 意味着每个集成都需要重复实现这些逻辑。

**方案**：`sdk/python/ai_platform_lab/` 独立包，httpx 为后端，OpenAI SDK 风格设计。

**Client 结构**：

```
Client(base_url, api_key, tenant_id)
  ├─ .chat                    → ChatResource
  │   └── .completions.create(model, messages)
  ├─ .rag                     → RagResource
  │   ├── .query(text, kb_id)
  │   ├── .upload(kb_id, file_path)
  │   └── .list_kbs()
  ├─ .agent                   → AgentResource
  │   ├── .run(session_id, message, tools)
  │   ├── .list_sessions()
  │   └── .get_session(id)
  ├─ .embedding               → EmbeddingResource
  │   ├── .create(model, texts)
  │   └── .list_models()
  ├─ .memory                  → MemoryResource
  │   └── list/get/create/delete CRUD
  └─ .orchestrator            → OrchestratorResource
      ├── .create_workflow(data)
      ├── .list_workflows()
      ├── .execute(id, inputs)
      └── .delete_workflow(id)
```

**同步 + 异步双客户端**：`Client`（httpx.Client 同步）+ `AsyncClient`（httpx.AsyncClient），资源方法差一个 `await`。httpx 保证同步/异步单代码路径，无 `asyncio` 导入污染同步路径。

**异常层次**：`AIPlatformError` → `APIError(status_code, message, body)` → `AuthenticationError`（401/403）/ `NotFoundError`（404）/ `RateLimitError`（429）。

**设计要点**：无 pydantic 运行时依赖（返回 dict，调用方自行 layer pydantic）、资源 property 每次访问新建（无过期凭证风险）、PEP 561 `py.typed` 标记支持类型检查、Python 3.9+ 兼容。

**独立包**：不导入 `apps.gateway` 或 `packages.*`，任何实现相同 REST 契约的服务端都可使用。

---

### 14.2 #46 — Console V2（React 管理界面）

**问题**：Phase D 的 Console 是 HTML stub（`apps/console/index.html`），只能查看基础状态，无法真正管理平台资源。

**方案**：React 18 + Vite 5 + TypeScript 5 + Ant Design 5 完整管理控制台，构建产物流入 `apps/console/static/`，通过 FastAPI `StaticFiles` 统一托管。

**技术栈选型**：

| 层次 | 选型 | 理由 |
|------|------|------|
| 框架 | React 18 + TypeScript strict | 类型安全，Concurrent mode |
| 构建 | Vite 5 | ESM 按需编译，毫秒级 HMR，Rollup Tree-shaking |
| UI | Ant Design 5 darkAlgorithm | 暗色主题开箱可用 |
| 数据 | @tanstack/react-query | server state 缓存 + 自动 refetch，比 Redux 少 60% 样板代码 |
| HTTP | axios + 拦截器 | 自动注入 `Authorization` + `X-Tenant-Id`，401 自动跳转登录 |
| 图表 | recharts | 折线图（QPS 趋势）+ 饼图（Token 分布） |
| 路由 | react-router-dom v6 + React.lazy | 代码分割 + lazy loading，Dashboard 的 recharts ~500KB 按需加载 |

**9 个管理页面**：Login（JWT 登录）→ Dashboard（统计卡 + 图表）→ Tenants（CRUD，admin）→ Agents（委托 + 版本历史）→ RAG（知识库 + 上传 + 查询测试）→ Memory（搜索 + 删除）→ Orchestrator（工作流 JSON 编辑器 + 执行）→ Audit（审计日志 + 工具分类）→ Settings（功能开关只读）。

**部署**：`npm run build` → `apps/console/static/` → FastAPI `app.mount("/console", StaticFiles(...))`。单 Docker 镜像同时托管 Python 后端和静态前端，无需 Nginx。

---

### 14.3 #47 — 评测 Pipeline + CI 门禁

**问题**：Phase E 已有 Agent 轨迹评测（5 条 baseline），但评测覆盖不全（缺少 RAG 质量、安全合规），且没有 CI 门禁保护 main 分支质量基线。

**方案**：三类基线数据集 ≥200 条 + `eval/pipeline.py` 统一评测 + `eval/gate.py` 门禁检查 + GitHub Actions 自动触发。

**三类基线**：

| 文件 | 类别 | 数量 | 覆盖场景 |
|------|------|------|---------|
| `eval/baselines/rag_extended.jsonl` | RAG | ≥100 | 事实/推理/多跳/负例/多语言/长上下文 |
| `eval/baselines/agent_scenarios.jsonl` | Agent | ≥50 | 工具调用/多步/澄清/拒绝/安全 |
| `eval/baselines/safety.jsonl` | 安全 | ≥50 | PII/注入/越狱/有害/边界 |

**Pipeline 流程**：`load_baselines()` → `run_category()` → `run_all()` → `EvalReport` → `compare_to_baseline()` → `check_gate()` → `GateResult`。

**门禁逻辑**：当前 PR 的 pass_rate 相对 main 基线回退超过 `EVAL_GATE_THRESHOLD_PCT`（默认 5%）→ gate fail → exit 1 → CI block merge。

**降级**：无 `EVAL_API_KEY` 时跳过 live 用例（标记 skipped），不阻塞 PR 流程。

---

### 14.4 #48 — 反馈飞轮

**问题**：用户的使用反馈（回答不好、评分低）没有系统性地回流到改进流程中。运营不知道哪些场景在变差，Prompt 优化靠感觉而非数据。

**方案**：四阶段闭环——反馈采集 → 质量聚合与告警 → 差评入库与 LLM 建议 → 自动创建 A/B 实验。

**阶段 1 — 反馈采集**：用户点击 👍/👎 或 1-5 星评分 → `POST /internal/feedback/` → `FeedbackStore`（内存或 SQLite）。负面反馈自动触发 `ingest_to_eval()` 追加到 `eval/baselines/bad_cases.jsonl`。

**阶段 2 — 质量监控**：`QualityAggregator` 按滑动窗口（默认 5 分钟）计算满意度、差评率、均分趋势。`AlertChecker` 三种检查：满意度 < 0.7、差评数 > 10/窗口、均分下降 > 0.5。告警分 `warning` / `critical` 两级。

**阶段 3 — Prompt 建议**：`generate_prompt_suggestion()` 调 LLM（无 key 时返回模板），基于差评样本生成优化建议和预期影响。

**阶段 4 — A/B 实验**：`auto_create_experiment()` 复用 `packages.prompt.experiment.ExperimentStore`，新版本 50/50 分流。默认关闭（`FEEDBACK_LOOP_AUTO_EXPERIMENT=false`），需人工调用 `/experiment/{suggestion_id}` 触发，防止未经验证的 Prompt 上线。

**完整飞轮** `POST /internal/feedback-loop/cycle/{tenant_id}`：collect → ingest → suggest → [人工审核] → experiment。

---

### 14.5 Phase J 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| SDK 的 HTTP 客户端 | httpx（同步 + 异步） | 单代码路径无重复逻辑 |
| SDK 无 pydantic 运行时 | 返回 dict | 保持轻量，调用方自选 |
| SDK 资源 property 每次新建 | 无过期凭证风险 | 更改 client._api_key 自动传播 |
| Console 技术栈 | React + Vite + Ant Design | Vite HMR 极速，Ant Design 暗色主题 |
| Console 构建产物 | FastAPI StaticFiles 托管 | 单 Docker 镜像，无 Nginx |
| 评测三类基线 | RAG/Agent/Safety ≥200 | 覆盖核心场景 |
| 门禁阈值 5% | `EVAL_GATE_THRESHOLD_PCT` | 平衡灵敏度与误报率 |
| 反馈飞轮人工审核 | `AUTO_EXPERIMENT=false` 默认 | 防止无监督自动化引入回归 |
| 质量告警两级 | warning / critical | 渐进式响应 |
| LLM 建议 fallback | 无 key 时返回模板 | 不崩溃，保证可用性 |

---

## 15. Phase K：生产基础设施（#49～#52）

Phase K 在平台开发者体验之后，做 **生产基础设施**：对象存储统一抽象、Helm Chart K8s 部署、Multi-AZ 高可用、GPU 弹性调度。四个能力从"存储底座 → 部署编排 → 高可用 → 异构计算"四个维度将平台推上生产就绪水平。

```
Phase K
  ├─ #49 对象存储           — StorageBackend ABC，local/S3/OSS 三后端，工厂模式
  ├─ #50 Helm Chart         — 14 个模板 + HPA + 三层 Secret 管理 + Ingress TLS
  ├─ #51 Multi-AZ 高可用     — topologySpreadConstraints + Redis Sentinel + PG 流复制 + PDB + NetworkPolicy
  └─ #52 GPU 弹性调度        — 独立 GPU Deployment，多指标 HPA（CPU+GPU+QPS），模型预热 initContainer
```

---

### 15.1 #49 — 对象存储

**问题**：RAG 文件上传、审计归档、Memory 快照等场景都需要文件存储，但之前没有统一抽象层——本地开发用文件系统，生产用 S3/OSS，代码与存储后端耦合。

**方案**：`StorageBackend` 抽象基类 + 工厂模式 + 全局单例，支持三种后端运行时切换。

**三层后端架构**：

| 后端 | 依赖 | 适用场景 |
|------|------|---------|
| `LocalStorageBackend` | 无 | 开发/测试，metadata 通过 `.meta.json` sidecar 文件存储 |
| `S3StorageBackend` | boto3（懒加载） | AWS S3 及 MinIO/Ceph RGW 兼容协议 |
| `OSStorageBackend` | oss2（懒加载） | 阿里云 OSS |

**核心接口**：`put(key, data)` / `get(key)` / `delete(key)` / `list(prefix)` / `exists(key)` / `get_metadata(key)` / `presign_get(key, expires)`。异步接口（`asyncio.to_thread` 桥接同步 SDK）与 FastAPI 生态集成。

**预签名 URL**：S3/OSS 后端支持 `presign_get()` 生成临时可下载链接（默认 3600s 过期），本地后端返回 501。

**优雅降级**：存储未初始化 → 503；boto3/oss2 未安装 → 初始化时报带安装建议的错误；`get_storage()` 返回 None 而非抛异常。

---

### 15.2 #50 — Helm Chart

**问题**：之前部署依赖 `docker compose up` 或手写 K8s YAML，不适合生产级 K8s 环境——没有参数化模板、HPA 自动伸缩、Secret 管理和版本回滚。

**方案**：生产级 Helm Chart（`deploy/helm/ai-platform-lab/`），14 个模板文件覆盖全部组件，三层配置覆盖（values.yaml → values-prod.yaml → --set flags）。

**Chart 结构**：

| 模板 | 类型 | 说明 |
|------|------|------|
| `gateway-deployment.yaml` + `service.yaml` + `hpa.yaml` | Deployment | 网关无状态服务，HPA CPU 70%，2-10 副本 |
| `worker-deployment.yaml` + `hpa.yaml` | Deployment | 工作节点，HPA CPU 80%，1-5 副本 |
| `qdrant-statefulset.yaml` + `service.yaml` | StatefulSet | 有状态向量数据库，10Gi PVC |
| `redis-deployment.yaml` + `service.yaml` | Deployment | 缓存 + Session 存储 |
| `postgres-statefulset.yaml` + `service.yaml` | StatefulSet | 有状态关系数据库 |
| `ingress.yaml` | Ingress | 可选 TLS（cert-manager + Let's Encrypt） |
| `secret.yaml` | Secret | 三级管理策略：inline → secretKeyRef → External Secrets Operator |
| `configmap.yaml` | ConfigMap | 非敏感环境变量 |
| `_helpers.tpl` | Helper | 命名模板 + checksum 注解驱动滚动更新 |

**三种部署模式**：All-in-One（默认，内置 Qdrant/Redis/Postgres）、外部依赖（`*.external.url` 指向托管服务）、纯 Gateway。

**Secret 三级策略**：Level 1（inline values，仅开发）→ Level 2（`secretKeyRef` 引用已有 Secret，生产基础）→ Level 3（External Secrets Operator + Vault/AWS Secrets Manager，企业级）。`helm.sh/resource-policy: keep` 注解防止误 `uninstall` 泄露 Secret。

---

### 15.3 #51 — Multi-AZ 高可用

**问题**：单 AZ（可用区）故障——节点宕机、机房维护——会导致服务完全不可用。生产部署需要在至少 2 个 AZ 间打散副本。

**方案**：`values-multi-az.yaml` overlay，通过 topologySpreadConstraints、Redis Sentinel、Postgres 流复制、PDB、NetworkPolicy 实现 ≥N+1 冗余。

**AZ 间打散——topologySpreadConstraints**：

```
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
```

`maxSkew: 1` 保证任意两个 AZ 间 Pod 数量差不超过 1，`DoNotSchedule` 硬约束而非尽最大努力。对比 `podAntiAffinity`（只禁止同节点），topologySpreadConstraints 控制的是 AZ 级别的分布。

**Redis Sentinel 自动故障转移**：3 个 Sentinel Pod 各部署在不同 AZ，`quorum=2` 避免网络抖动误判。Sentinel 使用 `SENTINEL get-master-addr-by-name` 命令发现当前主节点，应用层通过 Sentinel 模式直连（redis-py 原生支持）。

**Postgres 流复制**：`pg_basebackup` initContainer 引导 replica，WAL shipping 流式复制。故障转移需手动 `SELECT pg_promote()`——这是有意设计，避免自动 Promote 导致 split-brain。生产可叠加 Patroni 实现自动故障转移。

**PodDisruptionBudget**：Gateway `minAvailable: 2` / Worker `minAvailable: 1` / Qdrant `minAvailable: 1`，防止 `kubectl drain` 违反最小可用副本约束。

**NetworkPolicy**：限制 Pod 间流量，Gateway Egress 仅允许到 Qdrant/Postgres/Redis/DNS/外部 HTTPS。需 CNI 支持（Calico/Cilium/AWS VPC CNI）。

**混沌测试**：`deploy/k8s/chaos-test.yaml` 使用 ChaosMesh 模拟 AZ 故障，验证 AZ 挂掉 30s 时 Gateway 健康检查仍正常。

---

### 15.4 #52 — GPU 弹性调度

**问题**：Embedding 和 Rerank 服务运行在 CPU 节点上，推理速度慢、无法利用 GPU 加速；且 GPU 资源静态分配，低峰期浪费成本。

**方案**：独立的 GPU Deployment + 多指标 HPA（CPU + GPU + QPS） + 模型预热 initContainer，通过 `values-gpu.yaml` overlay 叠加部署。

**独立 Deployment 设计**：Embedding（port 8100）和 Rerank（port 8200）作为独立 K8s Deployment 运行，而非 Gateway 进程内加载。独立部署带来：独立 GPU 调度和节点亲和性、独立 HPA 策略、独立滚动更新（模型升级不重启 Gateway）。

**GPU 节点隔离**：GPU 节点打 taint `nvidia=:NoSchedule` + label `accelerator: nvidia`。GPU Pod（embedding/rerank）带 toleration + nodeSelector 只能调度到 GPU 节点；CPU Pod（gateway/worker）不带 toleration 被 taint 挡在 GPU 节点外——双向隔离。

**多指标 HPA v2**：

| 指标 | 来源 | 目标值 | 作用 |
|------|------|--------|------|
| CPU 利用率 | `resource.cpu` | 70% | 通用负载信号 |
| GPU 利用率 | `containerResource nvidia.com/gpu` | 70% | GPU 显存压力信号 |
| Gateway QPS | External metric（需 Prometheus Adapter）| 100 req/s | 最直接的负载先行指标 |

HPA 取三个指标中要求的最大副本数（保守扩容）。缩容稳定窗口 300s 防止 GPU 冷启动抖动。

**模型预热 initContainer**（冷启动 mitigation）：initContainer 下载模型权重到共享 `emptyDir` 卷 + 执行 dummy 推理触发 CUDA kernel 编译。无预热时首次请求延迟 30-90s，预热后 <100ms。代价是 Pod 启动时间增加 30-60s，在 60s 稳定窗口内可接受。

**成本优化**：`min_replicas: 1` 确保空闲时段最小 GPU 开销（T4 ~$0.35/hr，Embedding + Rerank = 2 GPU ~$0.70/hr）；`max_replicas: 8` 应对峰值。开发用 T4（16GB VRAM），生产用 A100（40/80GB VRAM），通过 `gpu-type` label 区分。

---

### 15.5 Phase K 设计要点

| 决策 | 选型 | 理由 |
|------|------|------|
| 存储抽象层 | ABC + 工厂模式 | 新增后端不修改调用方（开闭原则） |
| S3/OSS SDK 懒加载 | asyncio.to_thread 桥接 | 可选依赖不强制安装 |
| 存储优雅降级 | get_storage() 返回 None | 不影响其他模块可用性 |
| Helm 三层配置 | values.yaml → overlay → --set | 开发/测试/生产环境差异化 |
| Secret 三级管理 | inline → k8s Secret → External Operator | 从开发到企业级渐进升级 |
| Multi-AZ 打散 | topologySpreadConstraints vs podAntiAffinity | AZ 级分布而非节点级 |
| Postgres 手动故障转移 | `pg_promote()` 而非自动 | 防止 split-brain |
| GPU 独立 Deployment | 非 Gateway 进程内加载 | 独立伸缩/升级/调度 |
| 多指标 HPA | CPU + GPU + QPS 取 max | 保守扩容，覆盖多维度 |
| 模型预热 initContainer | 共享 emptyDir 卷 | 首请求 90s → <100ms |

---

## 16. 演进脉络总结

从第 2 周到 Phase K，完整的演进线如下：

| 阶段 | 核心交付 | 工程成熟度 |
|------|---------|-----------|
| 第 2 周 | RAG 索引管道 | 内部接口，同步/异步混合 |
| 第 3 周 | RAG 查询 API + 拒答 | 对外 API，有质量底线 |
| 第 4 周 | Agent ReAct 骨架 | 可运行但无持久化 |
| 第 5 周 | Tracing + Metrics + Eval | 可观测 + 回归检测 |
| Phase A | Redis + Worker + 审计 + CI | 多人内测可用 |
| Phase B1 | Token 计量 + 预算拦截 | 可计费、可控制成本 |
| Phase B2 | Vault + BM25 + Jaeger | 生产就绪增强 |
| Phase B3 | Rerank + KB 金丝雀 | 检索质量 + 灰度发布 |
| Phase C | 供应商矩阵 + Region + 自助 + 市场 | 平台化管理面 |
| Phase D | 熔断 + JWT + Console + 守卫 + 账单 | 运维与治理 |
| Phase E | 轨迹评测 + 意图路由 + 预算 + 质量门 + HITL | Agent 效果深化 |
| Phase F | Prompt 版本化 + A/B 实验 + 长记忆 + MCP + 压缩 | 能力中台补全 |
| Phase G | 语义缓存 + Embedding 独立服务 | 模型服务增强 |
| Phase H | 控制流编排 + Multi-Agent + 生命周期 + HITL 完整版 | Agent 高阶能力 |
| Phase I | 沙箱隔离 + 动作审计 + PII 脱敏 + OAuth2/mTLS | 安全与合规 |
| Phase J | Python SDK + Console V2 + 评测门禁 + 反馈飞轮 | 平台开发者体验 |
| Phase K | 对象存储 + Helm Chart + Multi-AZ + GPU 调度 | 生产基础设施 |

每个阶段都在前一个阶段的基础上解决一个维度的不足：**功能 → API 质量 → 运行时可靠性 → 可观测 → 工程硬化 → 生产力 → 平台化 → 运维与治理 → Agent 效果深化 → 能力中台补全 → 模型服务增强 → Agent 高阶能力 → 安全与合规 → 平台开发者体验 → 生产基础设施**。