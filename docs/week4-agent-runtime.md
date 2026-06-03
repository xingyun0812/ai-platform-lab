# 第 4 周：Agent 运行时骨架

学习计划见 [AI中台学习执行手册](./AI中台学习执行手册.md) 第 4 周。  
构建思路与代码导读见 [agent-build-and-code-guide.md](./agent-build-and-code-guide.md)。

---

## 目标

平台向 Agent 能力：**工具注册表**、**租户工具白名单**、**最大步数 / 工具超时 / 重试**、**内存会话**；对外 `POST /v1/agent/run`，返回 `final_message` 与 `tool_calls` 轨迹。

---

## 内置工具

| 工具名 | 说明 | 典型用途 |
|--------|------|----------|
| `calc` | 安全 AST 算术 `+ - * /` | 确定性计算 |
| `get_kb_snippet` | 调用第 2 周向量检索 | 知识库片段 |
| `httpbin_delay` | 请求 `httpbin.org/delay/{n}` | 超时与重试验证 |

工具定义见 `packages/agent/registry.py`（`name`、`description`、`parameters_schema`、`handler`）。

---

## 租户工具授权矩阵

配置：`config/tenants.yaml` 中 `allowed_tools`。**空列表 = 可用全部注册工具。**

| tenant_id | allowed_tools | 说明 |
|-----------|---------------|------|
| `demo-a` | `get_kb_snippet`, `calc` | 无 `httpbin_delay`（成本/风险敏感） |
| `demo-b` | `calc` | 仅计算器 |
| `admin` | `[]`（全部） | 开发自用 |

未授权工具若被模型请求 → **HTTP 403** `AGENT_TOOL_FORBIDDEN`（模型侧通常看不到未授权工具）。

---

## 策略配置

`config/agent.yaml` / `.env`：

| 项 | 默认 | 含义 |
|----|------|------|
| `AGENT_MAX_STEPS` | 8 | LLM ↔ 工具 循环上限 |
| `AGENT_TOOL_TIMEOUT_SECONDS` | 10 | 单工具执行超时 |
| `AGENT_TOOL_MAX_RETRIES` | 1 | 工具失败重试次数 |
| `AGENT_MODEL` | 同 `DEFAULT_MODEL` | 须支持 **function calling** |

---

## API：`POST /v1/agent/run`

鉴权：`X-Tenant-Id` + `Authorization: Bearer`。

### 请求体

```json
{
  "tenant_id": "admin",
  "session_id": "sess-demo-1",
  "messages": [{"role": "user", "content": "计算 (12+8)*2"}],
  "kb_id": "lab-demo",
  "model": null
}
```

- `messages`：**本轮新增**消息（通常一条 user），服务端拼接到同 `session_id` 历史。  
- `kb_id` 可选：写入 system 提示，引导 `get_kb_snippet` 使用。

### 成功响应（HTTP 200）

```json
{
  "tenant_id": "admin",
  "session_id": "sess-demo-1",
  "final_message": "结果是 40。",
  "tool_calls": [
    {
      "tool_name": "calc",
      "arguments": {"expression": "(12+8)*2"},
      "status": "success",
      "result": "{\"expression\": \"(12+8)*2\", \"result\": 40.0}",
      "error": null,
      "latency_ms": 0.5,
      "attempt": 0
    }
  ],
  "steps": 2,
  "model": "gpt-4o-mini",
  "trace_id": "..."
}
```

### 错误码

| HTTP | code | 场景 |
|------|------|------|
| 403 | `AGENT_TOOL_FORBIDDEN` | 租户无权使用该工具 |
| 422 | `AGENT_MAX_STEPS` | 超过最大步数 |
| 429 | `QUOTA_EXCEEDED` | 日配额 |
| 503 | `AGENT_UPSTREAM_ERROR` / `AGENT_RUN_ERROR` | LLM 或内部错误 |

工具超时：**不崩溃**；`tool_calls[].status=failed`，`error` 含超时说明，模型可继续基于 error 回复。

---

## 演示命令

```bash
export GW=http://127.0.0.1:8000
export H1="X-Tenant-Id: admin"
export H2="Authorization: Bearer sk-tenant-admin-change-me"

# 计算
curl -s "$GW/v1/agent/run" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","session_id":"s1","messages":[{"role":"user","content":"请用 calc 计算 (12+8)*2"}]}' | jq .

# 知识库（需已索引 lab-demo）
curl -s "$GW/v1/agent/run" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","session_id":"s2","kb_id":"lab-demo","messages":[{"role":"user","content":"知识库里 RAG 管道是什么？请用 get_kb_snippet 查 lab-demo"}]}' | jq .

# 会话记忆：第二轮只发新 user，应能引用上一轮
curl -s "$GW/v1/agent/run" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","session_id":"s1","messages":[{"role":"user","content":"上一轮算式里的第一个数字是多少？"}]}' | jq .

# 工具超时（将 AGENT_TOOL_TIMEOUT_SECONDS=3 后重启）
curl -s "$GW/v1/agent/run" \
  -H "Content-Type: application/json" -H "$H1" -H "$H2" \
  -d '{"tenant_id":"admin","session_id":"s3","messages":[{"role":"user","content":"请调用 httpbin_delay 延迟 10 秒"}]}' | jq '.tool_calls'

# demo-a 无 httpbin 工具（模型工具列表中不应出现 httpbin_delay）
curl -s "$GW/v1/agent/run" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: demo-a" \
  -H "Authorization: Bearer sk-tenant-demo-a-change-me" \
  -d '{"tenant_id":"demo-a","session_id":"a1","messages":[{"role":"user","content":"1+1"}]}' | jq .
```

---

## 验收对照

| 手册项 | 验证 |
|--------|------|
| 工具超时清晰、不崩 | `httpbin_delay` + 低 `AGENT_TOOL_TIMEOUT_SECONDS` |
| demo-a 禁止工具 | 授权矩阵无 `httpbin_delay`；伪造场景见导读 |
| session 多轮记忆 | 同一 `session_id` 连续两次请求 |

---

## 代码结构

| 路径 | 职责 |
|------|------|
| `packages/agent/registry.py` | 工具注册表 |
| `packages/agent/tools/builtin.py` | 三个 handler |
| `packages/agent/runner.py` | LLM ↔ 工具循环 |
| `packages/agent/session.py` | 内存会话 |
| `apps/gateway/agent/routes.py` | HTTP 入口 |

---

*文档版本：v1*
