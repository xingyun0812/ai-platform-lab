# Phase H 构建思路与代码导读：Agent 高阶能力

> 规格书：[orchestrator](./phase-h-orchestrator.md) · [multi-agent](./phase-h-multi-agent.md) · [lifecycle](./phase-h-agent-lifecycle.md) · [hitl](./phase-h-hitl.md)

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

Phase H 在 Phase G 模型服务增强之后，做 **Agent 高阶能力**：控制流编排、Multi-Agent 协作、Agent 生命周期管理、HITL 完整版。四个能力让 Agent 从"单轮工具调用"升级到"可编排、可协作、可版本管理、有人审批"的**生产级 Agent 平台**。

| Issue | 能力 | 核心文件 | 接入方式 |
|-------|------|----------|----------|
| #37 | 控制流编排 | `packages/agent/orchestrator/engine.py` | REST API `/internal/orchestrator/*` |
| #38 | Multi-Agent | `packages/agent/multi_agent/delegation.py` | REST API `/internal/agents/*` + orchestrator `agent_call` 节点 |
| #39 | Agent 生命周期 | `packages/agent/lifecycle/registry.py` | REST API `/internal/agent-lifecycle/*`（管理 API） |
| #40 | HITL 完整版 | `packages/hitl/service.py` | REST API `/internal/hitl/*` + runner 拦截高风险工具 |

**依赖关系**：orchestrator → multi_agent（`agent_call` 节点委托子 Agent）；HITL 从 Phase E 的 JSON stub 升级为完整版。

### #37 — 控制流编排

**问题**：Agent 只能线性 ReAct 循环，无法表达复杂工作流——先检索、再分类、然后根据结果走不同分支。

**方案**：DAG 工作流引擎，支持 10 种节点类型，可组合任意复杂流程。

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
4. `condition` 节点直接跳转 `branch.target`
5. 到达 `end_node` 返回 `ExecutionResult`

**模板语法** `${node_id.field}`：节点输出可被后续节点引用，实现数据流传递。

**沙箱安全**：`evaluate_condition()` 用 `eval()` + 词法过滤 + 限制命名空间，禁止 `import/exec/eval/open/__/lambda`。

**安全限制**：
| 限制 | 默认值 |
|------|--------|
| 最大节点执行数 | 100 |
| 总超时 | 300s |
| 并行最大分支 | 5 |

### #38 — Multi-Agent 协作

**问题**：单 Agent 上下文窗口有限，无法专业化分工。复杂任务需要 RAG 专家 + 代码审核 + 翻译等多个 Agent 协作。

**方案**：`AgentRegistry` + `delegate_to_agent()` 双组件架构。

**4 种 Agent 角色**：
| 角色 | 说明 | 典型场景 |
|------|------|---------|
| `primary` | 主 Agent，与用户直接交互 | 入口 Agent，协调其他子 Agent |
| `specialist` | 专家 Agent | RAG 检索、代码生成、翻译 |
| `reviewer` | 审核 Agent | 监督输出质量 |
| `router` | 路由 Agent | 分析意图并分发任务 |

**`AgentSpec` 核心字段**：
- `role`, `system_prompt`, `allowed_tools`, `model`
- `can_delegate` / `can_be_delegated_to` — 双向委托控制
- `max_delegation_depth` — 防止递归

**三重防递归机制**：
1. **委托栈**：每次委托记录栈，检测循环（A→B→A 拒绝）
2. **最大深度**：`max_delegation_depth`（默认 3）
3. **双向标志**：`can_delegate` + `can_be_delegated_to` 共同控制

**三种协作模式**：
1. **委托**：主 Agent → `delegate_to(agent_id, task)` → 子 Agent 执行 → 返回结果
2. **并行委托**：`parallel_delegate([{agent_id, task}, ...])` → `asyncio.gather` 并发执行
3. **链式**：编排引擎中 `agent_call` 节点串联多个 Agent

### #39 — Agent 生命周期管理

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

**持久化**：`config/agent_versions.yaml` + `data/agent_versions_overrides.json`（与 Prompt/MCP 同模式）。

**注意**：`traffic_split` 目前为元数据存储，实际流量路由需在调用层读取并按概率分配，本模块不实现路由逻辑。

### #40 — HITL 完整版

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

## 2. 使用链路

### 2.1 工作流执行（#37）

```mermaid
sequenceDiagram
  participant C as Client
  participant O as Orchestrator
  participant N as Nodes
  participant LLM as LLM
  participant MA as Multi-Agent

  C->>O: POST /workflows/{id}/execute
  O->>O: parse_workflow DAG
  loop 每节点
    alt llm_call
      O->>N: _execute_llm_call
      N->>LLM: 调用
      LLM-->>N: 回复
      N-->>O: output
    else condition
      O->>N: evaluate_condition
      N-->>O: branch target
      O->>O: 跳转到目标节点
    else agent_call
      O->>MA: delegate_to_agent
      MA-->>O: DelegationResult
    end
  end
  O-->>C: ExecutionResult(final_output, trace)
```

### 2.2 Multi-Agent 委托（#38）

```mermaid
sequenceDiagram
  participant P as Primary Agent
  participant R as AgentRegistry
  participant S as Specialist

  P->>R: get_agent(rag_specialist)
  R-->>P: AgentSpec
  P->>P: 检查委托深度 + 循环
  P->>S: delegate_to_agent(task, ...)
  S->>S: run_agent(isolated session)
  S-->>P: DelegationResult(output)
  P->>R: mark_invoked
```

### 2.3 Agent 生命周期（#39）

```mermaid
sequenceDiagram
  participant Admin as Admin
  participant API as Lifecycle API
  participant R as AgentLifecycleRegistry

  Admin->>API: POST /agent-lifecycle/{id}/versions
  API->>R: register_version(spec_snapshot)
  R-->>API: version=2, status=draft

  Admin->>API: POST /versions/{vid}/activate
  API->>R: activate_version(strategy=canary)
  R->>R: v1→archived, v2→active
  R->>R: traffic_split={v1:90, v2:10}
  R-->>API: RolloutStatus

  Admin->>API: POST /agent-lifecycle/{id}/rollback
  API->>R: rollback_version()
  R->>R: active←previous
  R-->>API: 回滚成功
```

### 2.4 HITL 审批流（#40）

```mermaid
sequenceDiagram
  participant C as Client
  participant R as Agent Runner
  participant H as HITL Service
  participant A as Admin

  C->>R: POST agent run（高风险工具）
  R->>H: request_approval(tool, args)
  H->>H: 创建 PENDING → 发送 Webhook
  H-->>R: approval_id, status=pending
  R-->>C: 202 pending_approval

  A->>H: POST /hitl/approvals/{id}/approve
  H->>H: status=approved, decided_by=admin
  H-->>A: 200

  C->>R: POST agent run（带 approval_id）
  R->>H: check_approval(id)
  H-->>R: approved
  R->>R: 执行工具
  R-->>C: 200 正常响应
```

---

## 3. 代码导读（按文件）

### `packages/agent/orchestrator/graph.py`（#37 DAG 数据模型）

**222 行。**

| 数据结构 | 字段 |
|----------|------|
| `GraphNode` | `node_id`, `node_type`（10 种类型）, `config`, `description` |
| `GraphEdge` | `from_node`, `to_node`, `condition` |
| `Workflow` | `workflow_id`, `nodes`, `edges`, `start_node`, `end_node` |

**关键函数**：
- `validate_workflow(wf)` — 校验：start/end 节点存在且类型正确、节点 ID 唯一、边引用有效、按 node_type 校验 config（如 condition 需要 `branches`，loop 需要 `body` + `max_iterations > 0`）
- `parse_workflow(data)` — dict → Workflow 反序列化 + 校验

### `packages/agent/orchestrator/engine.py`（#37 执行引擎）

**367 行。**

**`ExecutionContext`**：运行时上下文，持有 `inputs`、`outputs`、`trace`、`current_node`。

**`execute_workflow()`** 主入口：
```python
async def execute_workflow(workflow, *, inputs, max_steps, timeout_seconds):
    validate_workflow(workflow)
    ctx = ExecutionContext(inputs=inputs)
    result = await traverse_workflow(workflow, ctx, ...)
    return result
```

**`traverse_workflow()`** 核心循环：
1. 从 `current` 节点开始
2. `get_executor(node_type)` 获取执行器
3. 执行节点，输出写入 `ctx.outputs[node_id]`
4. `_select_next_node()` 根据边和 condition 找下一节点
5. condition 节点直接返回 `branch.target`
6. 到达 end 或超时/超步数返回

**`execute_subgraph()`**：并行/循环节点的子图执行，共享 parent context，上限 50 步。

**`WorkflowTraversalPersister` 协议**：`after_advance` / `on_workflow_completed` / `on_node_failure_persist` / `after_error_redirect` 四个 checkpoint 钩子（扩展预留）。

### `packages/agent/orchestrator/nodes.py`（#37 节点执行器）

**520 行，10 个内置执行器。**

**`register_node_executor()` / `get_executor()`**：全局执行器注册表（`dict[str, Callable]`）。

**`render_template(template, context)`**：`${node_id.field}` 模板渲染，支持链式引用。

**`evaluate_condition(expr, context)`**：沙箱 eval，支持比较、布尔运算、`in` 和 `not`。

**10 个执行器**：
| 执行器 | 行为 |
|--------|------|
| `_execute_start` | 返回 `{}` |
| `_execute_end` | 返回 stop 信号 |
| `_execute_llm_call` | 渲染 prompt → `forward_with_model_router()` |
| `_execute_tool_call` | 调 `execute_tool(tool_name, arguments)` |
| `_execute_condition` | `evaluate_condition` 求值 → 返回分支名 |
| `_execute_parallel` | `asyncio.gather` 多子图 |
| `_execute_loop` | 子图循环，最多 max_iterations |
| `_execute_output` | 渲染 value 模板 |
| `_execute_plan_step` | Plan 阶段步骤 |
| `_execute_agent_call` | 委托 multi_agent `delegate_to_agent()` |

### `packages/agent/multi_agent/registry.py`（#38 Agent 注册表）

**331 行。**

**`AgentSpec`** 核心字段：
- `role`, `system_prompt`, `allowed_tools`, `model`
- `can_delegate`, `can_be_delegated_to`, `max_delegation_depth`
- `is_tool_allowed(tool_name)` 检查工具白名单

**`AgentStatus`**：运行时健康状态（`healthy`, `last_invoked`, `invocation_count`, `last_error`）。

**`AgentRegistry`**：YAML + JSON overrides 加载，`add_agent()` / `update_agent()` / `remove_agent()` + 健康跟踪。

### `packages/agent/multi_agent/delegation.py`（#38 委托逻辑）

**334 行。**

**`delegate_to_agent()`**：
```python
async def delegate_to_agent(*, agent_id, task, tenant_id, session_id, ...):
    spec = registry.get_agent(agent_id)
    # 1) 深度检查（max_delegation_depth）
    # 2) 循环检测（delegation_stack 查重）
    # 3) 构建 messages：system_prompt + task
    # 4) run_agent() 在隔离 SessionStore 中执行
    # 5) 结果写回 blackboard
    # 6) 更新 registry 健康状态
    return DelegationResult(output, usage, delegation_depth)
```

**`parallel_delegate()`**：`asyncio.gather` 并发执行多个委托，任一失败不影响其他。

**`resolve_delegation_tools()`**：AgentSpec 白名单与租户 ACL 取交集。

### `packages/agent/lifecycle/registry.py`（#39 生命周期注册表）

**507 行。**

**`AgentVersion`** 数据结构：`version_id`（UUID4）、`agent_id`、`version`（按 agent 自增）、`spec_snapshot`（AgentSpec.to_dict() 快照）、`status`（draft/active/archived）。

**`RolloutStatus`**：`active_version`、`previous_version`（用于回滚）、`strategy`、`traffic_split`。

**`RolloutStrategy`** 枚举：`ALL_AT_ONCE` / `BLUE_GREEN` / `CANARY`。

**`AgentLifecycleRegistry`** 关键方法：
| 方法 | 行为 |
|------|------|
| `register_version()` | 自增版本号，返回 draft |
| `activate_version(strategy)` | 旧 active → archived，新 active，设 traffic_split |
| `rollback_version()` | active ← previous |
| `set_traffic_split(splits)` | 更新流量分配 |
| `get_active()` | 获取当前 active 版本 |

### `packages/hitl/store.py`（#40 审批存储）

**338 行，双后端。**

**`ApprovalRequest`**：`request_id`（UUID）、`tenant_id`、`tool_name`、`arguments`、`status`（PENDING/APPROVED/REJECTED/TIMEOUT/CANCELLED）、`expires_at`。

**`InMemoryApprovalStore`**：`dict` + `RLock`。
**`SqliteApprovalStore`**：SQLite `hitl_approvals` 表，`arguments`/`metadata` JSON 序列化。

**`init_approval_store(database_url)`**：`sqlite:` → SqliteApprovalStore，否则 InMemory。

### `packages/hitl/service.py`（#40 业务逻辑）

**141 行。**

| 函数 | 行为 |
|------|------|
| `request_approval()` | 创建 PENDING 请求 + 可选 Webhook |
| `check_approval()` | 查询状态 |
| `approve()` / `reject()` | 决策记录 |
| `timeout_expired_requests()` | 扫描过期请求标记为 timeout |

### 读代码顺序

```
packages/agent/orchestrator/graph.py → engine.py → nodes.py →
packages/agent/multi_agent/registry.py → delegation.py →
packages/agent/lifecycle/registry.py →
packages/hitl/store.py → service.py → webhook.py
```

---

## 4. 设计决策

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

## 5. 操作命令

```bash
# #37 创建工作流（RAG + 审核流水线）
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "rag_review",
    "name": "RAG + 审核",
    "nodes": [
      {"node_id": "start", "node_type": "start"},
      {"node_id": "retrieve", "node_type": "tool_call", "config": {"tool_name": "get_kb_snippet", "arguments": {"query": "${input.query}"}}},
      {"node_id": "output", "node_type": "output", "config": {"value": "${retrieve.result}"}},
      {"node_id": "end", "node_type": "end"}
    ],
    "edges": [
      {"from_node": "start", "to_node": "retrieve"},
      {"from_node": "retrieve", "to_node": "output"},
      {"from_node": "output", "to_node": "end"}
    ],
    "start_node": "start",
    "end_node": "end"
  }' | jq .

# #37 执行工作流
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows/rag_review/execute \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"inputs": {"query": "什么是 RAG"}}' | jq .

# #38 注册 RAG 专家 Agent
curl -s -X POST http://127.0.0.1:8000/internal/agents \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "rag_specialist",
    "name": "RAG 专家",
    "role": "specialist",
    "system_prompt": "你 RAG 专家，基于检索片段回答",
    "allowed_tools": ["get_kb_snippet"],
    "can_delegate": false,
    "enabled": true
  }' | jq .

# #38 委托任务
curl -s -X POST http://127.0.0.1:8000/internal/agents/rag_specialist/delegate \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"task": "检索 RAG 相关资料"}' | jq .

# #39 注册 Agent 版本
curl -s -X POST http://127.0.0.1:8000/internal/agent-lifecycle/rag_specialist/versions \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"spec_snapshot": {"agent_id": "rag_specialist", "name": "RAG 专家 v2"}}' | jq .

# #39 激活版本（canary）
curl -s -X POST http://127.0.0.1:8000/internal/agent-lifecycle/versions/{version_id}/activate \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"strategy": "canary"}' | jq .

# #39 回滚
curl -s -X POST http://127.0.0.1:8000/internal/agent-lifecycle/rag_specialist/rollback \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #40 列出待审批请求
curl -s "http://127.0.0.1:8000/internal/hitl/approvals?status=pending" \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" | jq .

# #40 批准
curl -s -X POST http://127.0.0.1:8000/internal/hitl/approvals/{request_id}/approve \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"decided_by": "admin", "reason": "ok"}' | jq .

# 单元测试
python3 tests/test_orchestrator.py
python3 tests/test_multi_agent.py
python3 tests/test_agent_lifecycle.py
python3 tests/test_hitl.py

# 验收
python eval/acceptance_smoke.py
```

---

## 6. 自测用例

| # | 输入 | 预期 |
|---|------|------|
| 1 | POST workflow + execute | `final_output` + `trace` |
| 2 | condition 节点 false 分支 | 走 else 边 |
| 3 | parallel 节点 | 多分支并行执行 |
| 4 | POST agents delegate | 子 Agent 返回结果 |
| 5 | 超 `MULTI_AGENT_MAX_DEPTH` | 拒绝委托，返回错误 |
| 6 | POST lifecycle version + activate | active 版本切换 |
| 7 | traffic 10% canary | `traffic_split` 记录正确 |
| 8 | 高风险工具 agent run | `202 pending_approval` |
| 9 | POST hitl approve + 重跑 | 工具执行成功 |
| 10 | webhook test | HMAC 签名正确 |