# Phase O #89 — Multi-Agent v2（黑板 + Runner 委托）

> **Issue**：[#89](https://github.com/xingyun0812/ai-platform-lab/issues/89)  
> **状态**：✅ 已交付

## 是什么

Phase H 的 Multi-Agent 委托仅直连 LLM；O4 升级为：

1. **委托走完整 `run_agent()`** — 子 Agent 可 ReAct 调工具
2. **共享黑板** — 委托输出写入 `blackboard:{tenant}:{session}`
3. **Reviewer 模式** — `role=reviewer` 自动读取黑板再裁决

## API

```bash
# 委托（带 session 写黑板）
curl -s -X POST http://127.0.0.1:8000/internal/agents/rag_specialist/delegate \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -H "Content-Type: application/json" \
  -d '{"task":"检索 RAG 管道","session_id":"demo-ma"}'

# 查黑板
curl -s http://127.0.0.1:8000/v1/agent/blackboard/demo-ma \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me"
```

## 实现

| 文件 | 职责 |
|------|------|
| `packages/agent/multi_agent/blackboard.py` | Redis / 内存黑板 |
| `packages/agent/multi_agent/delegation.py` | `run_agent()` 委托 + 写黑板 |
| `apps/gateway/agent/routes.py` | `GET /v1/agent/blackboard/{session_id}` |

## 验证

```bash
python -m unittest tests.test_multi_agent_blackboard -v
python eval/agent_vertical_smoke.py
```

## 与 Phase H 文档关系

见 [phase-h-multi-agent.md](./phase-h-multi-agent.md) §6 — 已更新「已知限制」边界。
