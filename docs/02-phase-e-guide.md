# Phase E 构建思路与代码导读：Agent 效果深化

> 操作手册：[phase-e-agent-quality.md](./02-phase-e-agent-quality.md) · 前置：[Phase D](./02-phase-d-00-guide.md)

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

Phase E 在 Phase D 的 Agent 治理（熔断、Session、RBAC）基础上，做 **Agent 效果深化**：从"能跑"到"跑得准、跑得省、跑得可控"。

五波次全部在 `packages/agent/runner.py` 主循环挂载，每项可独立开关：

| 子项 | 能力 | 开关 | 接入点 |
|------|------|------|--------|
| E1 | 轨迹评测 | CLI `agent_run.py run` | 离线，不依赖 runner |
| E2 | 意图路由 + Tool-RAG | `AGENT_TOOL_ROUTING_ENABLED` | runner 中筛选 tools 参数 |
| E3 | 上下文预算 + 摘要 | `agent.yaml` budget 配置 | `assemble_llm_messages()` |
| E4 | 质量门 + 反思 | `quality_min_score` / `reflect_max_retries` | 工具结果后处理 + hint 注入 |
| E5 | HITL + Shadow | `risk.py` 分级 + `X-Agent-Shadow` 头 | 高风险工具拦截 / 全量只记录 |

### E1 — 轨迹评测

**为什么需要轨迹评测？** 之前的 RAG eval 只看 `final_message` 是否含关键词，但 Agent 行为正确性取决于**工具调用轨迹**——模型有没有选对工具、有没有误调禁用工具、第一步工具是否正确。

**五维度指标**：

| 指标 | 计算方式 |
|------|---------|
| `tool_precision_at_1` | 有 `expect_first_tool` 的用例中，第一步调对比例 |
| `needless_tool_rate` | `forbid_tools`/`expect_no_tools` 用例误调工具比例 |
| `missing_tool_rate` | `expect_tools` 但未全部出现的用例比例 |
| `arg_valid_rate` | 有工具调用的用例中，无参数校验失败比例 |
| `pass_rate` | 业务断言（含轨迹）总体通过比例 |

**baseline 文件**：`eval/agent_baseline.jsonl`（5 条用例），`validate-baseline` 命令无需 LLM Key，CI 中可离线校验。

### E2 — 意图路由 + Tool-RAG

**问题**：平台注册工具越多，LLM 的 `tools` 参数越长，选错概率越大。需要"只暴露和 query 有关的工具"。

**策略**（`config/agent_tool_routing.yaml`）：

| strategy | 行为 |
|----------|------|
| `intent` | 关键词匹配 → 命中某 intent 的候选工具列表 |
| `rag` | 词袋余弦重叠分数 → 取 Top-K |
| `both` | 先用 intent 再用 rag 补足 |

**白名单始终生效**：路由只缩小候选集（与白名单取交集），不会放行白名单外的工具。

### E3 — 上下文预算 + 滚动摘要

**问题**：Agent 多轮对话后 messages 膨胀 → 超 token 上限 → 被截断或报错。

```
assemble_llm_messages(state, new_messages)
  → 1. tool 结果截断（tool_result_max_chars 默认 2000）
  → 2. 保留最近 N 轮（keep_recent_turns）
  → 3. 拼接 [session_summary] 前缀
  → 4. 超 budget 时 drop_oldest_until_budget()
```

**滚动摘要**：每 `every_n_turns=6` 轮触发 `maybe_compact_session()` → 旧轮转摘要 → 保留最近 2 轮。

### E4 — 质量门 + 反思

**问题**：`get_kb_snippet` 可能返回低分或空结果，LLM 仍基于差数据作答。

**实现**：

```
tool 执行 → parse_tool_result() 解析 ToolEnvelope
         → assess_tool_output() 检查 quality_score
           → low_quality → 注入 platform_quality_hint
             → LLM 收到 hint 后换检索词或告知证据不足
           → passed → 正常继续
```

`reflect_max_retries=2` 限制反思轮数，避免死循环。

### E5 — HITL + Shadow Agent

| 场景 | 动作 |
|------|------|
| `risk_level=high` 工具（如 httpbin_delay） | 拦截 → 返回 `202 pending_approval` |
| 管理员 `POST confirm` | 更新 JSON → runner resume 执行 |
| 请求头 `X-Agent-Shadow: true` | 全量记录但不执行 → `shadow_tool_calls` |

HITL 审批记录持久化到 `data/agent_approvals.json`，Phase H 会升级为独立数据库。

---

## 2. 使用链路

### 2.1 Agent Run 全链路（E2～E5）

```mermaid
sequenceDiagram
  participant C as Client
  participant R as runner
  participant TR as tool_router
  participant LLM as LLM
  participant H as HITL

  C->>R: POST agent run
  R->>TR: 意图筛候选工具
  R->>LLM: messages + 候选 tools
  LLM-->>R: tool_call
  alt 高风险工具
    R->>H: create pending
    R-->>C: 202 pending_approval
  else 低风险
    R->>R: 执行工具写回 messages
    R-->>C: answer + tool_trace
  end
```

### 2.2 E1 离线轨迹评测

```mermaid
flowchart LR
  CLI["eval/agent_run.py run"] --> API["POST /v1/agent/run"]
  API --> CHECK["expect_tools forbid_tools"]
  CHECK --> REPORT["pass/fail 报告"]
```

---

## 3. 代码导读（按文件）

### `packages/agent/tool_router.py`（E2 意图路由 + Tool-RAG）

**228 行，核心函数 `select_tools_for_query()`。**

```python
def select_tools_for_query(query, *, registry, allowed_tools, routing_enabled, rag_enabled):
    allowed = _allowed_name_set(registry, allowed_tools)  # 与白名单取交集
    if not routing_enabled or total <= 1:
        return ToolRoutingResult(strategy="none")  # 不启用则全暴露

    # strategy="intent": 遍历 config intents，关键词匹配得分最高者
    # strategy="rag": 词袋余弦重叠 score(query_tokens, tool_name+description)
    return ToolRoutingResult(tool_names=candidates, strategy=..., scores=...)
```

**关键结构** `ToolRoutingResult`：

| 字段 | 含义 |
|------|------|
| `tool_names` | 筛选后的候选工具列表 |
| `strategy` | 实际使用的策略（`none`/`intent`/`rag`/`intent+rag`） |
| `intent` | 匹配到的 intent 名称 |
| `scores` | Tool-RAG 的余弦相似度得分 |
| `filtered_count` | 被过滤掉的工具数 |

**配置文件** `config/agent_tool_routing.yaml`：

```yaml
strategy: intent
top_k: 5
intents:
  kb:
    keywords: ["文档", "知识库", "rag", "检索", "内容"]
    tools: ["get_kb_snippet"]
  calc:
    keywords: ["计算", "加", "减", "乘", "除", "数学"]
    tools: ["calc"]
```

**设计要点**：
- `_last_user_query()` 取最后一条 user 消息作为路由输入
- `routing_meta()` 将结果转为 `_platform.tool_routing` 响应字段
- `merge_pinned_tools()` 让 Plan 阶段的 `tool_hint` 强制并入，不会在路由阶段被过滤

### `packages/agent/context_budget.py`（E3 上下文预算）

**200 行，四个核心函数。**

| 函数 | 职责 | 关键参数 |
|------|------|---------|
| `truncate_tool_messages()` | 单个 tool 结果超 `max_chars` 的截断加 `...[tool_result_truncated]` 标记 | `max_chars`（默认 2000） |
| `maybe_compact_session()` | 每 N 轮触发：旧轮次转摘要，保留最近 K 轮 | `every_n_turns=6`, `keep_recent_turns=2` |
| `drop_oldest_until_budget()` | 从 `pinned_prefix` 后逐条删除最旧消息直到 token 估计不超 budget | `budget`, `pinned_prefix` |
| `assemble_llm_messages()` | 编排入口：截断 → 保留 N 轮 → 拼摘要前缀 → 超 budget 丢弃 | 全部以上参数 |

**Token 估算策略**：`estimate_tokens(text) = max(1, len(text) // 4)`——按字符数/4 粗估，不引入 tokenizer。
**摘要实现**：`stub_summarize()` 拼接摘要 + 最近 N 轮 role:snippet，截断到 2000 字符（Phase F 会升级为 LLM 摘要）。

**响应字段** `_platform.context_budget`：
```python
{
    "budget": 32000,
    "estimated_tokens": 12000,
    "truncated_messages": 5,     # 因超 budget 删除了 5 条
    "truncated_tool_results": 0,  # 无 tool 结果被截断
    "summary_applied": True,      # 挂了 [session_summary] 前缀
    "remaining": 20000            # 剩余预算
}
```

**SessionState**（`session_state.py`）数据结构：
```python
@dataclass
class SessionState:
    messages: list[dict]    # 翻转后的扁平消息列表
    summary: str | None     # 滚动摘要
    turn_count: int         # 轮数计数器
```
`split_turns()` / `flatten_turns()` 在 messages 和 (user+assistant) turn 之间做转换。

### `packages/agent/tool_envelope.py`（E4 质量门前置）

**70 行，工具结果的标准信封格式。**

```json
{"ok": true, "data": ..., "error_code": null, "quality_score": 0.85}
```

所有工具 handler 返回 `success_envelope(data, quality_score=...)` 或 `failure_envelope(error_code=..., message=...)`。

| 函数 | 用途 |
|------|------|
| `parse_tool_result(raw)` | JSON 解析 → `ToolEnvelope(ok, data, error_code, quality_score)` |
| `with_quality_hint(raw, hint)` | 注入 `platform_quality_hint` 后重新封包 |

非 JSON 返回值视为 `ok=True, quality_score=1.0`，保持向后兼容。

### `packages/agent/quality_gate.py`（E4 质量门 + 反思）

**39 行。**

```python
def assess_tool_output(tool_name, raw, *, min_score):
    env = parse_tool_result(raw)
    if not env.ok:
        return env, "failed"
    if tool_name == "get_kb_snippet":
        # 检查 snippets 非空且最高 score >= min_score（默认 0.3）
        # 低分 → "low_quality"
    return env, "passed"
```

**反思机制**：runner 收到 `low_quality` 后：
1. 在 tool 结果中注入 `platform_quality_hint`（提示 LLM 证据质量低）
2. 消耗一次 `reflect_max_retries`（默认 2）
3. 放回 messages → LLM 可重新生成或告知用户
4. `_platform.reflect_remaining` 反映剩余反思次数

`tool_calls[].quality_gate` 字段标记每步：`"passed"` / `"low_quality"` / `"failed"`。

### `packages/agent/risk.py`（E5 风险分级）

```python
def tool_requires_hitl(tool_name: str) -> bool:
    meta = load_tool_catalog().get(tool_name)
    risk = str(meta.get("risk_level") or "low").lower()
    return risk == "high"
```

风险级别来自 `config/tools_marketplace.yaml` 中每个工具的 `risk_level` 字段。

### `packages/agent/hitl.py`（E5 HITL 审批流）

**225 行，JSON 文件持久化 + Phase H 委托 shim。**

| 操作 | 函数 |
|------|------|
| 创建审批 | `create_pending_execution()` → 生成 UUID，写入 `data/agent_approvals.json` |
| 查询 | `get_approval(approval_id)` → 读 JSON 文件 / 委托 Phase H 数据库 |
| 确认 | `confirm_execution()` → 更新 status 为 `confirmed` |
| 拒绝 | `reject_execution()` → 更新 status 为 `rejected` |
| 列表 | `list_pending(tenant_id)` → 返回待审批列表 |

**设计要点**：
- `HITL_ENABLED=true` 时优先委托 `packages.hitl`（Phase H 升级路径）
- 审批状态机：`pending → confirmed | rejected`
- runner 收到 `202 pending_approval` 后返回客户，客户端后续 `GET /internal/agent/approvals/{id}` 轮询

### `packages/agent/shadow.py`（E5 Shadow）

**34 行。**

```python
def shadow_tool_record(tool_name, arguments) -> tuple[str, ToolCallRecord]:
    payload = success_envelope({"shadow": True, "executed": False, ...})
    record = ToolCallRecord(tool_name, status="success", quality_gate="skipped")
    return payload, record
```

当请求头 `X-Agent-Shadow: true` 时：
- 全量遍历工具但不执行
- 构建 `shadow_tool_calls` 注入响应
- `quality_gate="skipped"` 跳过质量门

### `eval/agent_run.py`（E1 轨迹评测 CLI）

**547 行，Agent 领域完整的 eval pipeline。**

```
# 验证 baseline 结构（CI 使用，无需 LLM Key）
python eval/agent_run.py validate-baseline

# 全量运行（需要 Gateway + LLM_API_KEY）
python eval/agent_run.py run --run-id e1-baseline --min-pass-rate 0.8

# 对比两次报告
python eval/agent_run.py compare before.json after.json
```

**核心流程**：
1. `validate_agent_baseline()` 离线校验 JSONL 结构（必填字段、expect/expect_error_code 匹配、tenant_id 有效性）
2. `run_agent_baseline()` 遍历每条用例 → `POST /v1/agent/run` → 收集响应
3. `evaluate_agent_case()` 评估：检查状态码、tool_calls 轨迹、final_message 断言
4. `aggregate_trajectory_metrics()` 汇总五维度指标
5. `compare_reports()` 对比两次报告 pass_rate delta + 翻转用例

**报告结构**：`eval/runs/agent/{run_id}.json`，含 `summary`、`agent_metrics`（仅四率）、`results`（每条详细）。

### 读代码顺序

```
runner.py → tool_router.py → context_budget.py → quality_gate.py → hitl.py → eval/agent_run.py
```

---

## 4. 设计决策

| 决策 | 选型 | 理由 |
|------|------|------|
| 轨迹 eval 独立于主 runner | 离线 CLI 脚本 | 无需启动服务即可 validate-baseline，CI 友好 |
| 意图路由用关键词而非 embedding | 轻量、零依赖 | 意图数量有限（计算/检索/闲聊），关键词匹配足够 |
| Tool-RAG 用词袋余弦 | 无需外部向量库 | 与 BM25 tokenize 复用，计算快 |
| Token 估算用 `len//4` | 粗估而非 tokenizer | 无需加载 tokenizer 模型，启动快 |
| 摘要用 stub 拼接 | 简单字符串裁剪 | Phase E 先验证流程，Phase F 升级 LLM 摘要 |
| quality_score 内嵌在工具结果 | ToolEnvelope 标准格式 | 工具自身对质量最清楚，无需外部模型判断 |
| HITL 用 JSON 文件持久化 | 零外部依赖 | Phase H 升级为数据库时接口不变 |

---

## 5. 操作命令

```bash
# E1 验证 baseline 格式（无需服务）
python eval/agent_run.py validate-baseline

# E1 运行 Agent 轨迹评测（需 Gateway + LLM_API_KEY + 已索引 lab-demo）
python eval/agent_run.py run --run-id e1-release --min-pass-rate 0.8

# E1 对比两次评测
python eval/agent_run.py compare eval/runs/agent/before.json eval/runs/agent/after.json

# E2 启用意图路由（默认 enabled）
export AGENT_TOOL_ROUTING_ENABLED=true

# E2 启用 Tool-RAG（默认仅 intent）
export AGENT_TOOL_RAG_ENABLED=true

# E4 查看反思次数
# 响应中 _platform.reflect_remaining

# E5 高风险工具测试
curl -s -X POST http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "test-hitl-1",
    "messages": [{"role": "user", "content": "调一下 httpbin_delay 延迟 2 秒"}]
  }' | jq .

# E5 Shadow 模式（加请求头）
# 在请求头中加入 X-Agent-Shadow: true

# 全部验收
python eval/acceptance_smoke.py
```

---

## 6. 自测用例

| # | 输入 | 预期 |
|---|------|------|
| 1 | agent run calc 题 | tool_trace 含 calc |
| 2 | AGENT_TOOL_ROUTING_ENABLED | `_platform.tool_routing` 有候选 |
| 3 | 超长 session | context_budget 裁剪 |
| 4 | KB 空结果 + quality_gate | reflect_remaining > 0 |
| 5 | 高风险工具 httpbin_delay | 202 pending_approval |
| 6 | confirm approval 重跑 | 工具执行成功 |
| 7 | X-Agent-Shadow true | shadow_tool_calls 有记录未执行 |
| 8 | agent_run.py run | baseline 通过率 |
| 9 | forbid_tools 用例 | eval fail |
| 10 | expect_tools 用例 | eval pass |
