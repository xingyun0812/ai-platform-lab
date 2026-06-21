# Phase H — Multi-Agent 协作框架（#38）

> **目标**：基于 #37 控制流编排引擎，实现主 Agent 委托子 Agent 协作。

对标「Agent 平台架构全景」中的「Agent 应用层 — Multi-Agent」能力。

---

## 1. 设计要点

### 1.1 Agent 角色

| 角色 | 说明 | 典型场景 |
|------|------|---------|
| `primary` | 主 Agent（用户直接交互） | 入口 Agent，协调其他子 Agent |
| `specialist` | 专家 Agent | RAG 检索、代码生成、翻译等单一领域 |
| `reviewer` | 审核 Agent | 监督其他 Agent 输出，质量把关 |
| `router` | 路由 Agent | 分析意图，分发任务到专家 Agent |

### 1.2 AgentSpec 数据模型

```python
@dataclass
class AgentSpec:
    agent_id: str               # 唯一标识
    name: str                   # 显示名
    role: str                    # primary | specialist | reviewer | router
    description: str
    system_prompt: str           # Agent 的 system prompt
    model: str | None            # None → 用 default_model
    allowed_tools: list[str]     # 工具白名单（空 = 允许所有）
    # 委托限制
    can_delegate: bool           # 是否允许委托其他 Agent
    can_be_delegated_to: bool    # 是否可被其他 Agent 委托
    max_delegation_depth: int    # 最大委托深度（防递归）
    enabled: bool
```

### 1.3 协作模式

#### 委托（Delegation）
```
主 Agent → delegate_to("rag_specialist", "检索 RAG 资料")
         ← DelegationResult(output="...")
```

#### 并行委托
```
主 Agent → parallel_delegate([
    {"agent_id": "rag_specialist", "task": "检索 A"},
    {"agent_id": "code_reviewer", "task": "审核代码"},
])
         ← [DelegationResult, DelegationResult]
```

#### 链式（基于编排引擎）
```yaml
nodes:
  - {node_id: start, node_type: start}
  - {node_id: step1, node_type: agent_call, config: {agent_id: rag_specialist, task: "..."}}
  - {node_id: step2, node_type: agent_call, config: {agent_id: code_reviewer, task: "审核 ${step1.output}"}}
  - {node_id: end, node_type: end}
```

#### 监督
```yaml
nodes:
  - {node_id: start, node_type: start}
  - {node_id: worker, node_type: agent_call, config: {agent_id: specialist, task: "..."}}
  - {node_id: reviewer, node_type: agent_call, config: {agent_id: reviewer, task: "审核 ${worker.output}"}}
  - {node_id: end, node_type: end}
```

### 1.4 防递归机制

- **委托栈**：每次委托记录栈，检测循环（A→B→A 拒绝）
- **最大深度**：`max_delegation_depth` 限制（默认 3）
- **可委托标志**：`can_delegate` / `can_be_delegated_to` 双向控制

### 1.5 工具白名单

- `allowed_tools = []` → 允许所有工具
- `allowed_tools = ["get_kb_snippet"]` → 仅允许指定工具
- AgentSpec 的 `is_tool_allowed(tool_name)` 检查

### 1.6 集成点

#### orchestrator 新节点类型 `agent_call`

```yaml
- node_id: call_rag
  node_type: agent_call
  config:
    agent_id: rag_specialist
    task: "检索关于 ${input.topic} 的资料"  # 支持 ${var} 模板
    inputs:                                   # 额外输入
      context: ${previous.output}
    timeout: 60
```

执行后 `outputs.call_rag` 包含：
```json
{
  "agent_id": "rag_specialist",
  "task": "...",
  "status": "completed",
  "output": "Agent 的回复内容",
  "usage": {...},
  "delegation_depth": 1,
  "execution_time_ms": 1234.5
}
```

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| GET | `/internal/agents` | 任何已认证 | 列出所有 Agent |
| GET | `/internal/agents/{id}` | 任何已认证 | 详情 |
| POST | `/internal/agents` | admin | 注册 |
| PATCH | `/internal/agents/{id}` | admin | 更新 |
| DELETE | `/internal/agents/{id}` | admin | 删除 |
| POST | `/internal/agents/{id}/delegate` | 任何已认证 | 委托任务 |

### 使用示例

```bash
# 1. 注册 RAG 专家 Agent
curl -s -X POST http://127.0.0.1:8000/internal/agents \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "rag_specialist",
    "name": "RAG 专家",
    "role": "specialist",
    "system_prompt": "你是 RAG 专家，基于检索片段回答问题",
    "allowed_tools": ["get_kb_snippet"],
    "can_delegate": false,
    "enabled": true
  }'

# 2. 委托任务
curl -s -X POST http://127.0.0.1:8000/internal/agents/rag_specialist/delegate \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"task": "检索 RAG 相关资料", "timeout_seconds": 60}'
```

### 在编排引擎中使用

```bash
# 创建包含 agent_call 的工作流
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "rag_review_pipeline",
    "name": "RAG + 审核",
    "nodes": [
      {"node_id": "start", "node_type": "start"},
      {
        "node_id": "retrieve",
        "node_type": "agent_call",
        "config": {"agent_id": "rag_specialist", "task": "检索 ${input.query}"}
      },
      {
        "node_id": "review",
        "node_type": "agent_call",
        "config": {"agent_id": "reviewer", "task": "审核 ${retrieve.output}"}
      },
      {
        "node_id": "final",
        "node_type": "output",
        "config": {"value": "${review.output}"}
      },
      {"node_id": "end", "node_type": "end"}
    ],
    "edges": [
      {"from_node": "start", "to_node": "retrieve"},
      {"from_node": "retrieve", "to_node": "review"},
      {"from_node": "review", "to_node": "final"},
      {"from_node": "final", "to_node": "end"}
    ],
    "start_node": "start",
    "end_node": "end"
  }'

# 执行
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows/rag_review_pipeline/execute \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{"inputs": {"query": "什么是 RAG"}}'
```

---

## 3. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MULTI_AGENT_ENABLED` | `true` | 总开关 |
| `AGENTS_CONFIG_PATH` | `config/agents.yaml` | YAML 默认 |
| `AGENTS_OVERRIDES_PATH` | `data/agents_overrides.json` | JSON overrides |
| `MULTI_AGENT_DEFAULT_TIMEOUT` | `60` | 单次委托默认超时 |
| `MULTI_AGENT_MAX_DEPTH` | `3` | 委托最大深度 |

---

## 4. 测试与验收

```bash
# 1. 单元测试（20 个用例）
python3 tests/test_multi_agent.py
# 期望：20/20 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  Multi-Agent AgentSpec + 工具白名单   PASS
# PF  Multi-Agent REST API CRUD            PASS
```

---

## 5. 代码导航

```
packages/agent/multi_agent/
├── __init__.py           # 包导出
├── registry.py          # Agent 注册表
│   ├── AgentSpec            # 数据模型
│   ├── AgentStatus          # 运行时状态
│   ├── AgentRegistry        # 注册表（YAML + JSON）
│   ├── init_agent_registry() # 全局单例
│   └── get_agent_registry()
└── delegation.py        # 委托逻辑
    ├── DelegationResult     # 委托结果
    ├── delegate_to_agent()   # 单次委托
    └── parallel_delegate()   # 并行委托

packages/agent/orchestrator/nodes.py
└── _execute_agent_call()    # agent_call 节点执行器

apps/gateway/multi_agent_routes.py  # REST API
config/agents.yaml                   # 种子配置
```

---

## 6. 已知限制（面试时主动说）

1. **无 Agent 间消息传递**：当前仅主→子单向委托；无子→主回询。生产应加双向通信。
2. **无共享黑板**：多 Agent 并行时无共享上下文；只能通过编排引擎的 `outputs` 传递。
3. **委托无持久化**：委托结果仅返回不存储；生产应加 execution history。
4. **无 Agent 版本管理**：AgentSpec 无版本；改 system_prompt 即时生效。生产应加版本表。
5. **LLM 调用直连**：委托时直接调 `forward_with_model_router`，未走 Agent runner 的完整 ReAct 循环。
6. **无并行委托编排节点**：`parallel_delegate` 仅在代码层；编排引擎中需用 `parallel` 节点 + 多个 `agent_call` 子图实现。

---

## 7. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| 未来 | Agent 间双向通信（消息队列） | — |
| 未来 | 共享黑板（Blackboard 模式） | — |
| 未来 | Agent 版本管理 + 灰度发布 | #39 |
| 未来 | 委托执行历史持久化 | — |
| 未来 | 委托走完整 Agent runner（ReAct 循环） | — |

---

## 8. 面试讲法

1. **为什么需要 Multi-Agent**：单 Agent 上下文窗口有限；复杂任务需分工（RAG 专家 + 代码审核 + 翻译）。
2. **角色设计**：4 种角色覆盖主从协作、专家分工、监督审核、路由分发场景。
3. **防递归**：委托栈 + 最大深度 + 双向可委托标志，三重保护防止递归爆炸。
4. **工具白名单**：AgentSpec 限制子 Agent 可用工具，实现最小权限原则。
5. **集成编排引擎**：`agent_call` 节点让 Agent 委托成为工作流的一等公民，可组合任意复杂协作。
6. **诚实边界**：无双向通信、无共享黑板、委托直连 LLM（未走完整 ReAct）；这些是生产化需补的。

参考代码：
- `packages/agent/multi_agent/registry.py:30` — AgentSpec
- `packages/agent/multi_agent/registry.py:90` — AgentRegistry
- `packages/agent/multi_agent/delegation.py:60` — delegate_to_agent
- `packages/agent/multi_agent/delegation.py:170` — parallel_delegate
- `packages/agent/orchestrator/nodes.py:_execute_agent_call` — 编排集成
- `apps/gateway/multi_agent_routes.py` — REST API
