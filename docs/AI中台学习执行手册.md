# AI 中台学习执行手册（跟做版）

> **适用**：后端经验约 10 年+，目标 **平台 / 中台** 向 AI 应用研发；Python 能读懂、日常可用 AI 辅助写代码；**未做过** 传统检索/推荐业务。  
> **主线**：做一个可演示的 **「最小 AI 中台」**（网关 + RAG 流水线 + Agent 运行时骨架 + 观测评测），用交付物驱动学习。  
> **原则**：先 **工程与治理**（租户、配额、观测、回归），再 **效果细节**（chunk、重排）；语言不追求手写炫技，追求 **边界清晰、可测、可运维**。

---

## 0. 开始前（第 0 天，约 2～4 小时）

### 0.1 环境

- [x] 本机：Docker（可选但强烈建议）、`python 3.11+`、`git` ✅ 2026-05-12
- [ ] 账号：任一 OpenAI 兼容 API（官方 / 国内聚合 / 自建 vLLM 均可）
- [x] 向量库：本地选 **Chroma** 或 **Qdrant**（二选一，8 周内不换） ✅ 2026-05-12

### 0.2 仓库与目录约定

新建 mono-repo 或单仓库，建议目录：

```text
ai-platform-lab/
  apps/gateway/          # FastAPI：对外统一入口
  apps/worker/           # 异步：索引、评测任务（可与 gateway 同进程起步，后续拆）
  packages/contracts/    # pydantic 模型：请求/响应/错误码
  packages/observability/# trace_id、结构化日志封装
  eval/                    # 评测集 JSONL + 运行脚本
  docs/                    # 接入说明、架构图
```

### 0.3 定义三个「假租户」

在配置里写死即可（后续再考虑 DB）：

| tenant_id | 日配额（请求数） | 允许模型            | 备注     |
| ----------- | ---------------- | ------------------- | -------- |
| `demo-a`    | 1000             | 你默认用的 cheap 模型 | 模拟成本敏感 |
| `demo-b`    | 200              | 同一模型或另一别名   | 模拟限额紧 |
| `admin`     | 无限（开发自用） | 任意                | 仅本机   |

**本日交付**：仓库初始化 + 目录存在 + `README.md` 里写明三租户与目标。

---

## 第 1 周：LLM Gateway（统一接入）

### 目标

对外暴露 **OpenAI 兼容** 的 `POST /v1/chat/completions`（可先只做非流式，周末加流式），内层转发真实供应商；带上 **租户鉴权、配额扣减、结构化日志、trace_id**。

### 任务清单

- [ ] FastAPI 项目可运行；健康检查 `GET /healthz`
- [ ] 请求头：`X-Tenant-Id` + `Authorization: Bearer <tenant_token>`（token 映射可先写死在配置）
- [ ] 配额：内存计数即可（进程重启清零可接受）；返回 **429** 时 body 带统一错误结构
- [ ] 超时与重试：上游 5xx / 网络错误，**有限次退避**；记录最终失败原因
- [ ] 日志：每条请求 **trace_id**（`uuid4`）、tenant、模型名、latency、status
- [ ] **密钥**：业务侧示例脚本里不出现明文 Key，只调用你的 gateway

### 验收（自查）

1. 用 `curl` 调 gateway，能完成一次对话。  
2. 伪造 `demo-b` 超限，得到 **429** + 统一 JSON 错误。  
3. 日志里能搜到同一 `trace_id` 的请求开始与结束。

### 本周交付物

- `docs/week1-gateway.md`：接口说明 + 错误码表 + 本地启动命令

---

## 第 2 周：RAG 数据管道（异步索引 + 版本）

### 目标

把「文档进知识库」做成 **平台能力**：上传/指定路径 → **异步任务** → chunk → embed → 写入向量库；支持 **kb_id + version**（版本可先手动 bump）。

### 任务清单

- [ ] 定义资源：`kb_id`（知识库）、`version`（整数或语义化字符串）
- [ ] 任务表或内存队列：任务状态 `pending/running/success/failed` + 错误信息
- [ ] Chunk：固定 `chunk_size` / `overlap` 写配置即可；记录每 chunk 的 `source_uri` 与 `offset`
- [ ] Embedding：走 gateway 或直连同一供应商（二选一，**全仓库统一一种**）
- [ ] 检索 API（对内）：`POST /internal/retrieve` → 返回 `chunks[]`（含 `score`、元数据）

### 验收

1. 同一 `kb_id` 连续 `v1` → `v2` 两次索引，检索可指定版本（或默认最新）。  
2. 故意损坏一份输入文件，任务 **failed** 且错误可查询。  
3. 检索结果里 **每条** 带 `kb_id`、`version`、`chunk_id`。

### 本周交付物

- `docs/week2-rag-pipeline.md`：数据流说明 + 任务状态查询方式

---

## 第 3 周：RAG 服务化 + 质量底线

### 目标

对外提供 `POST /v1/rag/query`：**检索 → 拼上下文 → LLM 回答**；落实 **空检索拒答、低分阈值、引用片段**（引用可先粗粒度：返回 `chunk_id` 列表）。

### 任务清单

- [ ] 请求体：`tenant_id`、`kb_id`、`version?`、`query`、`top_k`
- [ ] 阈值：`min_score` 以下不送入 LLM，返回固定业务错误码（自定义，与 HTTP 状态分离）
- [ ] Prompt 模板：单独文件或配置，便于后续评测对比
- [ ] （可选）混合检索：BM25 + 向量 **二选一先做透**，另一个标记 TODO

### 验收

1. 问一个 **库里没有** 的问题：应 **拒答或明确说无依据**，不胡编。  
2. 同一问题连打 10 次，**trace** 可区分每次检索耗时与总耗时。  
3. 改 `top_k` 或阈值，**eval** 里至少一条用例行为变化可观察（为下周铺垫）。

### 本周交付物

- `eval/baseline.jsonl`：至少 **30 条**（`query` + `expect`：应命中/应拒答/应包含某关键词）

---

## 第 4 周：Agent 运行时骨架（工具注册表 + 策略）

### 目标

**不是**炫多智能体剧情，而是平台能力：**工具注册**、**按租户授权**、**超时/失败兜底**、**单次会话状态**（内存或 Redis 二选一，优先简单）。

### 任务清单

- [ ] 工具接口抽象：`name`、`description`、`json_schema`、`handler`
- [ ] 内置 2～3 个假工具：例如 `get_kb_snippet`（调上周检索）、`httpbin_delay`（测超时）、`calc`（确定性）
- [ ] `POST /v1/agent/run`：输入 `messages` + `tenant_id` + `session_id`；输出 `final_message` + `tool_calls` 轨迹数组
- [ ] 策略：**最大步数**、单工具 **超时**、失败 **重试次数**；超限则终止并返回可观测错误
- [ ] 租户工具白名单：`demo-a` 只能用其中两个工具等（写配置）

### 验收

1. 故意让工具超时，响应里带 **清晰错误** 且不全站崩溃。  
2. `demo-a` 调被禁止的工具 → **403** 或业务错误码。  
3. 同一 `session_id` 连续两轮对话，**能利用上一轮 assistant 内容**（最小记忆）。

### 本周交付物

- `docs/week4-agent-runtime.md`：工具注册说明 + 租户授权矩阵

---

## 第 5 周：观测与评测回归（平台「质量部门」）

### 目标

把 **LangSmith / LangFuse / OpenTelemetry** 选一个接进关键路径（至少 gateway + RAG + agent 各打一条 span）；评测 **可脚本化**，结果落本地 SQLite 或 JSON 文件。

### 任务清单

- [ ] Trace：`trace_id` 与外部 tracing **关联**（propagation）
- [ ] Metrics（可先粗）：QPS、P95 延迟、按 `tenant_id` 聚合（可用 Prometheus 文本端点或简单 log metrics）
- [ ] `eval/run.py`：读 `eval/baseline.jsonl`，对每条调用 `rag/query` 或 `agent/run`，记录 **pass/fail** 与原因
- [ ] 对比：保存 `run_id`，两次运行能 diff **通过率**

### 验收

1. 人为改坏 prompt，评测 **通过率下降**，报告里可见。  
2. 线上模拟（本机）压 50 并发短跑，**无进程崩溃**；记录瓶颈在检索还是 LLM（文字结论即可）。

### 本周交付物

- `docs/week5-observability-eval.md`：如何开 trace、如何跑 eval、如何看对比

---

## 第 6 周：硬化与「平台叙事」文档

### 目标

**可演示、可投递、可面试讲**：README 架构图、多租户故事、降级策略、已知限制与路线图。

### 任务清单

- [ ] **Model Router**（简版）：主模型失败切备用；或按租户映射 `model` 别名
- [ ] 限流：租户级 **令牌桶**（仍可先内存）
- [ ] Docker Compose：gateway + 向量库 +（可选）worker；一键 `docker compose up`
- [ ] `docs/architecture.md`：Mermaid 一张 **分层图** + 数据流
- [ ] `docs/roadmap.md`：诚实写未做：计费精确到 token、多 region、权限审计细化等

### 验收

1. 同事（或未来的你）按 README **15 分钟内**跑通主路径。  
2. 你能用 **10 分钟**口述：租户、RAG 版本、Agent 工具治理、评测回归 —— 不卡壳。

### 本周交付物

- 公开到 GitHub 的 repo（或私有 + 面试前给只读权限）；**不要**提交真实 API Key

---

## 加分（时间有剩再选，不阻塞主线）

按性价比排序：

1. **MCP**：实现一个最小 MCP server，暴露 1 个只读工具，gateway 或 agent 能消费（体现「生态接入」叙事）。  
2. **vLLM / Ollama**：本地拉起一个轻量模型，gateway 增加 `provider=local` 路由，对比延迟与并发行为。  
3. **内容安全占位**：请求/响应过一遍正则或调用占位 API（接口留好，实现可 stub）。

---

## 每周节奏模板（复制到周报）

```markdown
## 第 X 周

- 本周目标（1 句话）：
- 完成任务（链接 PR / commit）：
- 演示命令（可复制）：
- 遇到问题与解决：
- 下周调整：
```

---

## 自检总表（做完打勾）

- [ ] 三租户配置与不同限额  
- [ ] Gateway：鉴权、配额、超时、统一错误体  
- [ ] RAG：异步索引、版本、检索对内 API  
- [ ] RAG：拒答/阈值/引用或 chunk 溯源  
- [ ] Agent：工具注册、租户白名单、最大步数、超时  
- [ ] 观测：trace 贯通主要路径  
- [ ] 评测：JSONL + 可重复运行 + 两次对比  
- [ ] Compose 或等价一键启动  
- [ ] 文档：架构 + 接入 + Roadmap  
- [ ] 仓库无密钥泄露  

---

## 附录：技术选型锁定（避免中途换栈）

| 类别       | 建议锁定（8 周内不改）        |
| ---------- | ----------------------------- |
| Web        | FastAPI                       |
| 向量库     | Chroma **或** Qdrant 二选一   |
| HTTP 客户端 | httpx + 明确 timeout          |
| 配置       | pydantic-settings + `.env`    |
| 评测格式   | JSONL，每行一个 case          |

---

*文档版本：v1 | 与聊天上下文对齐：平台/中台向「最小 AI 中台」主线。*
