# Phase H — 控制流编排引擎（#37）

> **目标**：支持 DAG + 条件分支 + 循环 + 并行执行的 Agent 工作流引擎。

对标「Agent 平台架构全景」中的「Agent 应用层 — 控制流编排」能力。是 **#38 Multi-Agent 框架** 的前置依赖。

---

## 1. 设计要点

### 1.1 数据模型

```python
@dataclass
class GraphNode:
    node_id: str
    node_type: str   # start | end | llm_call | tool_call | condition | parallel | loop | output
    config: dict     # 类型特定配置
    description: str

@dataclass
class GraphEdge:
    from_node: str
    to_node: str
    condition: str | None  # 条件表达式；None = 无条件

@dataclass
class Workflow:
    workflow_id: str
    name: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    start_node: str
    end_node: str
```

### 1.2 节点类型

| 节点类型 | 说明 | config 关键字段 |
|---------|------|----------------|
| `start` | 入口 | — |
| `end` | 出口 | — |
| `llm_call` | 调用 LLM | `prompt`, `model`, `variables` |
| `tool_call` | 调用 Agent 工具 | `tool_name`, `arguments` |
| `condition` | 条件分支 | `branches: [{condition, target}]`, `default` |
| `parallel` | 并行 fan-out + gather | `branches: [{id, subgraph}]`, `gather: "all" \| "first"` |
| `loop` | 循环 | `body: subgraph`, `max_iterations`, `break_condition` |
| `output` | 输出 | `value`（支持 `${node_id.field}` 模板） |

### 1.3 执行模型

1. 从 `start_node` 开始
2. 拓扑遍历：执行节点 → 评估出边条件 → 选择下一节点
3. 节点输出写入 `ExecutionContext.outputs[node_id]`
4. `condition` 节点：从 `output.branch` 直接跳转（无需显式边）
5. 到达 `end_node` 返回

### 1.4 模板渲染

支持 `${node_id.field.subfield}` 引用：

```yaml
# 引用前序节点输出
config:
  value: "结果：${llm1.content}"
  prompt: "基于 ${retrieve.context} 回答 ${input.query}"
```

### 1.5 条件表达式（沙箱 eval）

```python
# 支持的语法
${n1.score} > 0.8                      # 比较
${n1.content} == "yes"                  # 字符串相等
${n1.a} == 1 and ${n1.b} == 2          # 布尔运算
not ${n1.status} == "error"            # not
${n1.items} in ["a", "b"]              # in

# 禁止的关键字
import, exec, eval, open, __, lambda, globals, locals
```

### 1.6 安全限制

| 限制 | 默认值 | 配置项 |
|------|--------|--------|
| 最大节点执行数 | 100 | `ORCHESTRATOR_MAX_STEPS` |
| 总超时 | 300s | `ORCHESTRATOR_TIMEOUT_SECONDS` |
| 并行最大分支 | 5 | `ORCHESTRATOR_MAX_PARALLEL_BRANCHES` |
| 子图最大步数 | 50 | 硬编码 |

---

## 2. REST API

| Method | Path | 权限 | 说明 |
|--------|------|------|------|
| POST | `/internal/orchestrator/workflows` | admin | 创建工作流 |
| GET | `/internal/orchestrator/workflows` | 任何已认证 | 列出 |
| GET | `/internal/orchestrator/workflows/{id}` | 任何已认证 | 详情 |
| DELETE | `/internal/orchestrator/workflows/{id}` | admin | 删除 |
| POST | `/internal/orchestrator/workflows/{id}/execute` | 任何已认证 | 执行 |
| GET | `/internal/orchestrator/examples` | 无需认证 | 示例模板 |

### 使用示例

```bash
# 1. 创建工作流
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "rag-pipeline",
    "name": "RAG 管道",
    "nodes": [
      {"node_id": "start", "node_type": "start"},
      {
        "node_id": "retrieve",
        "node_type": "tool_call",
        "config": {"tool_name": "get_kb_snippet", "arguments": {"query": "${input.query}"}}
      },
      {
        "node_id": "answer",
        "node_type": "output",
        "config": {"value": "${retrieve.result}"}
      },
      {"node_id": "end", "node_type": "end"}
    ],
    "edges": [
      {"from_node": "start", "to_node": "retrieve"},
      {"from_node": "retrieve", "to_node": "answer"},
      {"from_node": "answer", "to_node": "end"}
    ],
    "start_node": "start",
    "end_node": "end"
  }'

# 2. 执行工作流
curl -s -X POST http://127.0.0.1:8000/internal/orchestrator/workflows/rag-pipeline/execute \
  -H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"query": "什么是 RAG"}}'
```

### 条件分支示例

```json
{
  "workflow_id": "router",
  "name": "意图路由",
  "nodes": [
    {"node_id": "start", "node_type": "start"},
    {
      "node_id": "classify",
      "node_type": "llm_call",
      "config": {"prompt": "分类用户意图：${input.query}"}
    },
    {
      "node_id": "route",
      "node_type": "condition",
      "config": {
        "branches": [
          {"condition": "${classify.content} == \"rag\"", "target": "rag_branch"},
          {"condition": "${classify.content} == \"code\"", "target": "code_branch"}
        ],
        "default": "default_branch"
      }
    },
    {"node_id": "rag_branch", "node_type": "output", "config": {"value": "RAG 处理"}},
    {"node_id": "code_branch", "node_type": "output", "config": {"value": "代码处理"}},
    {"node_id": "default_branch", "node_type": "output", "config": {"value": "默认处理"}},
    {"node_id": "end", "node_type": "end"}
  ],
  "edges": [
    {"from_node": "start", "to_node": "classify"},
    {"from_node": "classify", "to_node": "route"},
    {"from_node": "rag_branch", "to_node": "end"},
    {"from_node": "code_branch", "to_node": "end"},
    {"from_node": "default_branch", "to_node": "end"}
  ],
  "start_node": "start",
  "end_node": "end"
}
```

---

## 3. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ORCHESTRATOR_ENABLED` | `true` | 总开关 |
| `ORCHESTRATOR_WORKFLOWS_PATH` | `config/orchestrator_workflows.yaml` | YAML 默认 |
| `ORCHESTRATOR_OVERRIDES_PATH` | `data/orchestrator_overrides.json` | JSON overrides |
| `ORCHESTRATOR_MAX_STEPS` | `100` | 最大节点执行数 |
| `ORCHESTRATOR_TIMEOUT_SECONDS` | `300` | 总超时 |
| `ORCHESTRATOR_MAX_PARALLEL_BRANCHES` | `5` | 并行最大分支 |

---

## 4. 测试与验收

```bash
# 1. 单元测试（18 个用例）
python3 tests/test_orchestrator.py
# 期望：18/18 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  编排引擎 DAG 模型 + 校验      PASS
# PF  编排引擎 REST API + 执行      PASS
```

---

## 5. 代码导航

```
packages/agent/orchestrator/
├── __init__.py              # 包导出
├── graph.py                # DAG 数据模型
│   ├── GraphNode                # 节点
│   ├── GraphEdge                # 边
│   ├── Workflow                 # 工作流
│   ├── validate_workflow()      # 校验
│   └── parse_workflow()         # dict → Workflow
├── nodes.py                # 节点执行器
│   ├── register_node_executor()  # 注册执行器
│   ├── evaluate_condition()       # 沙箱条件求值
│   ├── render_template()          # ${var} 模板渲染
│   └── _execute_llm_call/tool_call/condition/parallel/loop/output
├── engine.py               # 执行引擎
│   ├── ExecutionContext          # 运行时上下文
│   ├── ExecutionResult           # 执行结果
│   ├── execute_workflow()        # 主执行函数
│   ├── execute_subgraph()        # 子图执行（parallel/loop）
│   └── _select_next_node()       # 下一节点选择
└── workflow_store.py       # 存储层
    ├── WorkflowStore             # YAML + JSON overrides
    └── init_workflow_store()     # 全局单例

apps/gateway/orchestrator_routes.py   # REST API
```

---

## 6. 已知限制（面试时主动说）

1. **无持久化执行状态**：工作流定义持久化，但执行状态（trace/outputs）仅返回不存储。生产应加 execution history。
2. **无断点续跑**：执行中断后无法从中间节点恢复。
3. **condition 跳转不检查目标存在性**：`branch.target` 指向不存在的节点会在下一轮报 NODE_NOT_FOUND。
4. **parallel 无错误隔离**：`gather="all"` 时某分支失败会记录 error 但不影响其他；`gather="first"` 会取消其他。
5. **无可视化**：无 DAG 图形编辑器；依赖外部工具（如 Mermaid）。
6. **条件 eval 用 `eval()`**：已做词法过滤 + 限制命名空间，但仍有风险。生产应换 AST 解析。

---

## 7. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| #38 | Multi-Agent 框架（基于编排引擎实现 Agent 间协作） | #37 ✅ |
| 未来 | 执行历史持久化 + 断点续跑 | — |
| 未来 | DAG 可视化编辑器 | Console V2 (#46) |
| 未来 | AST 条件解析（替代 eval） | — |
| 未来 | 工作流版本管理 | — |

---

## 8. 面试讲法

1. **为什么需要编排**：单轮 Agent 只能线性 ReAct；复杂任务需要 DAG（如 RAG → 分类 → 路由 → 多分支处理）。
2. **节点类型设计**：7 种节点覆盖 LLM 调用、工具调用、条件、并行、循环、输出，可组合任意复杂流程。
3. **模板渲染**：`${node_id.field}` 引用前序节点输出，实现数据流传递。
4. **沙箱 eval**：条件表达式用 `eval()` + 词法过滤 + 限制命名空间；生产应换 AST 解析。
5. **安全限制**：max_steps + timeout + max_parallel 防止死循环和资源爆炸。
6. **诚实边界**：无执行历史持久化、无断点续跑、无可视化；这些是生产化需补的。

参考代码：
- `packages/agent/orchestrator/graph.py:30` — GraphNode
- `packages/agent/orchestrator/nodes.py:120` — evaluate_condition
- `packages/agent/orchestrator/engine.py:60` — execute_workflow
- `packages/agent/orchestrator/engine.py:180` — _select_next_node
- `apps/gateway/orchestrator_routes.py` — REST API
