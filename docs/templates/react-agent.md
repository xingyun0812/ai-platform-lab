# ReAct Agent 模板

> ReAct (Reasoning + Acting)：LLM 在循环中思考→调用工具→观察结果→继续思考。

## 架构

```
用户输入
  │
  ▼
┌─────────────────────────────────────────────────────┐
│                 run_react_loop()                      │
│                                                       │
│  循环 (max_steps):                                    │
│    1. LLM.思考(next_action + tool_call)                │
│    2. 执行工具 (ToolRegistry)                          │
│    3. 观察结果                                        │
│    4. 决定继续或结束                                    │
└─────────────────────────────────────────────────────┘
  │
  ▼
最终回答
```

## 代码骨架

```python
from packages.agent.runner import run_agent
from packages.agent.session import SessionStore
from packages.agent.registry import ToolRegistry

result = await run_agent(
    tenant_id="admin",
    session_id="session-1",
    new_messages=[{"role": "user", "content": "计算 (1+2)*3"}],
    allowed_tools=("calc", "web_search"),
    allowed_models=(),
    model="gpt-4",
    session_store=SessionStore(),
    registry=ToolRegistry(),
)
print(result["final_message"])
```

## curl 调用

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "react-demo",
    "messages": [{"role": "user", "content": "计算 (1+2)*3 并搜索今天的天气"}]
  }'
```

## 关键配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `agent_max_steps` | 10 | 最大 ReAct 循环步数 |
| `agent_tool_timeout_seconds` | 30 | 工具调用超时 |
| `agent_context_token_budget` | 8000 | 上下文 token 预算 |
| `agent_reasoning_mode` | react | react / cot |

## 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/react_loop.py` | ReAct 主循环 |
| `packages/agent/runner.py` | Session + memory + billing wiring |
| `packages/agent/registry.py` | 工具注册 |
| `packages/agent/tools/` | 内置工具 |

## 与其它模式的关系

- ToT、Debate、Research 都**底层使用 ReAct** 作为 Agent 执行引擎
- ReAct 是最基础的 Agent 模式，其它模式在其上叠加搜索/编排
