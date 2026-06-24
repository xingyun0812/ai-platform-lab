# Phase O #88 — CoT 推理模式

> **Issue**：[#88](https://github.com/xingyun0812/ai-platform-lab/issues/88)  
> **状态**：✅ 已交付

## 是什么

在默认 `react` 模式不变的前提下，开启 `cot` 后 Agent 会在 `<thinking>...</thinking>` 中写出**可见的推理 trace**，再返回正文或工具调用。

## 配置

| 层级 | 键 | 值 |
|------|-----|-----|
| `config/agent.yaml` | `reasoning_mode` | `react`（默认） / `cot` |
| 环境变量 | `AGENT_REASONING_MODE` | 同上 |
| 请求体 | `reasoning_mode` | 单次覆盖 |

## API

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "cot-demo",
    "reasoning_mode": "cot",
    "messages": [{"role": "user", "content": "1+1 用 calc"}]
  }'
```

响应新增字段：

- `reasoning_mode`: `"cot"`
- `reasoning_trace[]`: `{ step, thinking, visible_content }`

## 实现

| 文件 | 职责 |
|------|------|
| `packages/agent/reasoning.py` | 解析 thinking、注入 CoT system |
| `packages/agent/runner.py` | 循环内剥离 thinking 并写入 trace |
| `packages/contracts/agent_schemas.py` | `ReasoningTraceRecord` |

## 验证

```bash
python -m unittest tests.test_agent_reasoning -v
python eval/agent_cot_smoke.py
```

## 诚实边界

- 未实现 streaming thinking（后续迭代）
- 依赖模型遵循 `<thinking>` 格式；无 thinking 时降级为普通正文
