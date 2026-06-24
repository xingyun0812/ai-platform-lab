# 面试叙事手册（10～15 分钟）

> **用途**：技术面试、架构评审、内部分享的统一口述稿。  
> **配套**：[demo-walkthrough.md](./demo-walkthrough.md)（动手演示）、[roadmap.md](./roadmap.md) §已知限制（诚实边界）。

---

## 一句话定位

这是一个 **从模型网关到生产基础设施的完整 AI 平台参考实现**：按 Phase A～M 渐进交付；Phase L 把 stub 做深，Phase M 把 **RAG 增量索引** 做到可演示、可观测；**Phase O** 把 Agent 对齐 JD2 智能体研发岗（Planner / CoT / Multi-Agent v2 / 外部工具 / 数据分析 vertical）。

---

## 10 分钟分层讲法

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'primaryBorderColor': '#58a6ff', 'lineColor': '#8b949e'}}}%%
flowchart TB
  subgraph L1["1. 模型服务层 ~2min"]
    G["Gateway + 路由 + 熔断"]
    B["计费 + 语义缓存"]
  end
  subgraph L2["2. 能力中台 ~2min"]
    R["RAG 版本 + 金丝雀"]
    P["Prompt A/B + MCP + Memory"]
  end
  subgraph L3["3. Agent 应用层 ~2min"]
    A["工具白名单 + 轨迹"]
    H["Orchestrator + Multi-Agent + HITL"]
  end
  subgraph L4["4. AgentOps ~2min"]
    O["OTel + Prometheus + Grafana"]
    E["Eval Pipeline + 反馈飞轮"]
  end
  subgraph L5["5. 生产形态 ~1min"]
    K["Helm + 多 AZ/GPU 模板"]
    C["Console V2 运营面"]
  end
  subgraph L6["6. 诚实边界 ~1min"]
    X["RBAC 浅 · 多 AZ 模板级"]
  end

  L1 --> L2 --> L3 --> L4 --> L5 --> L6

  style L6 fill:#3a2a1a,stroke:#fbbf24,color:#e6edf3
```

### 第 1 层：模型服务（~2 分钟）

**讲什么**：多租户 Gateway 统一 OpenAI 兼容协议；模型别名路由到不同上游；熔断 + fallback；按 token 落库与日/月预算。

**亮点**：
- 三租户 YAML 演示隔离（模型、工具 ACL、配额分层）
- `SEMANTIC_CACHE_ENABLED` 降本（exact 模式可无 LLM Key 演示命中）

**关键词**：`apps/gateway/model_router.py`、`packages/billing/`、`packages/semantic_cache/`

---

### 第 2 层：能力中台（~2 分钟）

**讲什么**：RAG 不是「调个向量库」，而是 **可版本化、可金丝雀、可 eval 对比** 的数据管道。

**SOP 故事**（大厂对齐）：
1. 索引 `kb_id + version`
2. 开金丝雀 `canary_percent`
3. `eval/run.py compare` 看 pass_rate
4. 达标全量 / 不达标回滚 `canary_percent=0`

**诚实说**：真 Rerank / LLM Judge / 金丝雀回滚已在 Phase L 落地；**Phase M** 补齐 BM25 按 source 差量、purge-source、`skipped_chunks` 指标与 demo 二次索引断言。

**Phase M 一句话**：同文件二次索引 → `skipped_chunks>=1` → BM25 不 scroll 全库 → Console 删文档同步清向量。

**关键词**：`packages/rag/source_index.py`、`packages/rag/index_metrics.py`、`eval/platform_demo.sh --with-llm`

---

### 第 3 层：Agent 应用（~2 分钟）

**讲什么**：Agent 不是裸调 LLM，而是 **网关 enforce 工具白名单 + 全链路审计**。

**亮点**：
- **Phase O**：LLM Task Planner（`auto_plan`）、CoT `reasoning_mode`、Multi-Agent 黑板、YAML 插件、`web_search` / `sql_query`、数据分析 vertical
- MCP 工具桥接（config 注册 → `mcp_{server}_{tool}`）
- destructive 工具 → HITL `202 pending_approval`
- Orchestrator workflow + Multi-Agent 委托 + **Vertical 演示链**（RAG + HITL + data-analysis-vertical）
- `tool_call_strategy=parallel` + Prometheus `agent_*` 指标（性能调优可讲）

**关键词**：`packages/agent/planner.py`、`packages/agent/reasoning.py`、`packages/agent/multi_agent/`、`eval/agent_jd2_gate.py`

---

### 第 4 层：AgentOps（~2 分钟）

**讲什么**：可观测 + 可回归 + 可改进。

**亮点**：
- OTel trace、Prometheus `/metrics`（含 `rag_index_*` 增量指标）、Grafana dashboard
- `baseline.jsonl` + CI 门禁（RAG + Agent 双 gate + **Phase O `agent_jd2_gate`**）
- 反馈飞轮：**live 已验**（#61 `feedback_loop_demo --live`）

**关键词**：`packages/observability/`、`eval/pipeline.py`、`packages/feedback/`

---

### 第 5 层：生产形态（~1 分钟）

**讲什么**：不是只能 `docker compose up`。

- Helm Chart、`values-multi-az.yaml`、`values-gpu.yaml`
- 对象存储抽象 local/s3/oss
- Console V2：http://127.0.0.1:8000/console/

**诚实说**：多 AZ/GPU 是 **模板级**，未在真实集群压测。

---

### 第 6 层：诚实边界（~1 分钟，主动说）

引用 [roadmap.md](./roadmap.md) §已知限制，核心三点：

1. **模块齐、部分仍浅**：细粒度 RBAC、生产级 DLP（增量索引 Phase M 已做满）
2. **opt-in 默认关**：沙箱、OAuth2、语义缓存、Memory Store
3. **非商业产品**：无发票、单进程开发默认；多 AZ/GPU 为 Helm 模板级

---

## 15 分钟演示路线（Console + curl）

| 分钟 | 动作 | 话术 |
|------|------|------|
| 0～2 | `./eval/platform_demo.sh --no-llm` | 自动化冒烟，Console API 全 200 |
| 2～4 | 登录 Console Dashboard | 运营面，非业务 App |
| 4～7 | RAG 索引 + **二次索引**（`skipped_chunks`） | Phase M 增量故事 |
| 7～10 | v2 + 金丝雀 + eval compare | SOP 核心（需 Key） |
| 11～13 | Audit / Agent vertical / 反馈飞轮 | 治理 + 闭环 |
| 13～15 | `python eval/sdk_smoke.py` 或 `./eval/sdk_pypi_smoke.sh --local` | SDK 三接口 · 可演示 pip 安装包 |

详见 [demo-walkthrough.md](./demo-walkthrough.md)。

---

## 高频 Q&A

### Q1：和 Dify / LiteLLM / Langfuse 比？

| 维度 | 本仓库 |
|------|--------|
| 定位 | **全栈参考实现**（网关 + RAG + Agent + Ops），非 SaaS |
| vs LiteLLM | 多了 RAG、Agent、租户治理、Console |
| vs Dify | 更偏 **平台工程/infra**，UI 是 Console 非工作流画布 |
| vs Langfuse | 内置 eval pipeline + 反馈飞轮，观测是一层不是全部 |

### Q2：为什么单进程 Gateway？

学习仓库优先 **可读懂**；Helm 已支持 K8s 水平扩展，Redis 共享配额。面试主动说边界。

### Q3：Rerank 为什么曾经是 stub？

Phase A～K 先打通链路；Phase L #54 已接真 provider，可用 `eval compare` 对比 stub vs api。

### Q4：测试怎么保证质量？

484+ 单测、无外部依赖可跑；live eval 需 Key；CI 跑 lint + acceptance_smoke + RAG/Agent gate + **`python eval/agent_jd2_gate.py`**。

### Q7：反馈飞轮怎么演示？

```bash
python eval/feedback_loop_demo.py --mock   # CI
python eval/feedback_loop_demo.py --live   # Gateway + admin token
```

live 路径：点踩 → `cycle` → `suggestion_id`（experiment 需先 apply suggestion）。

### Q5：Multi-Agent 和 Orchestrator 区别？

- **Orchestrator**：显式 workflow 步骤（DAG 式编排）
- **Multi-Agent**：Agent 间委托/通信，Phase O 起有 **共享黑板**（`GET /v1/agent/blackboard/{session_id}`）

### Q8：Phase O / JD2 智能体岗怎么讲？

> Planner 拆目标 → CoT 可选显式 thinking → Multi-Agent 黑板协作 → web_search/sql_query 外部工具 → `data-analysis-vertical` 一条业务演示链；CI 用 `eval/agent_jd2_gate.py` 离线跑通 O1～O10 单测矩阵。

```bash
python eval/agent_jd2_gate.py run
./eval/data_analysis_vertical.sh --mock
```

### Q6：HITL 怎么工作的？

destructive 工具调用返回 `202` + `approval_id`；审批后带 `approval_id` resume `/v1/agent/run`。

---

## 演示前检查清单

> **Live 验证**（2026-06-23）：`feedback_loop_demo --live` ✅ · `agent_vertical_smoke` 6/6 ✅ · `platform_demo --no-llm` / `--with-llm` ✅

> 环境细节：[local-llm-setup.md](./local-llm-setup.md)

```bash
# 无 Key 最小路径
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000
./eval/platform_demo.sh --no-llm   # 含 sdk smoke（skip 三接口）

# 有 Key 完整路径（Chat + Agent；RAG 需 embedding 另配）
# .env: LLM_BASE_URL=http://10.212.129.94:8090/v1
export LLM_API_KEY=sk-your-key-here
docker compose up -d qdrant redis postgres
./eval/platform_demo.sh --with-llm
python eval/sdk_smoke.py
```

| 检查项 | 期望 |
|--------|------|
| `/console/` | 200，React 非旧 stub |
| `admin` 登录 | Dashboard 有指标 |
| `platform_demo --no-llm` | exit 0 |
| `sdk_smoke`（无 Key） | exit 0，chat/rag/agent skipped |
| `platform_demo --with-llm` | exit 0（chat/agent；RAG 可能 LOW_CONFIDENCE） |
| `feedback_loop_demo --live` | `passed` + suggestion_id |
| `agent_vertical_smoke` | 6/6（无 Key 时 live vertical skip 不判失败） |
| `agent_jd2_gate` | exit 0（Phase O 离线矩阵） |

---

## 5 分钟 Agent JD2 路线（Phase O）

> 无 Key 可跑 mock 段；有 Key 可加 live plan / CoT。

| 分钟 | 命令 / 动作 | 话术 |
|------|-------------|------|
| 0～1 | `python eval/agent_jd2_gate.py run` | Phase O CI 门禁，覆盖 Planner/CoT/工具/vertical |
| 1～2 | `python eval/agent_planner_smoke.py` | LLM 结构化 Plan → 逐步执行 |
| 2～3 | `reasoning_mode=cot` on `/v1/agent/run`（需 Key） | `<thinking>` trace 可审计 |
| 3～4 | `./eval/data_analysis_vertical.sh --mock` | 搜索 → SQL → calc 业务 vertical |
| 4～5 | `curl .../metrics \| rg agent_` | 并行工具 + plan steps Prometheus |

详见 [demo-walkthrough.md](./demo-walkthrough.md) §Agent JD2。

## 相关文档

| 文档 | 用途 |
|------|------|
| [local-llm-setup.md](./local-llm-setup.md) | 内网 LLM 网关 + 三模型 + `.env` |
| [enterprise-ai-platform-sop.md](./enterprise-ai-platform-sop.md) | 大厂 SOP 对照 |
| [architecture.md](./architecture.md) | 架构全景 |
| [phase-l-priority-roi.md](./phase-l-priority-roi.md) | 下一步 ROI |
| [PROJECT_STATUS.md](./PROJECT_STATUS.md) | 一页纸状态 |
