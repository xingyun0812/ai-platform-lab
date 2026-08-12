# Phase L #59 — Agent Vertical 端到端演示

> **故事线**：Orchestrator 委托 RAG 专家 → Agent Run 触发 HITL → 审批续跑 → 审计落库

## 前置

```bash
# .env 关键开关（默认已开）
ORCHESTRATOR_ENABLED=true
MULTI_AGENT_ENABLED=true
HITL_ENABLED=true
AUDIT_ACTIONS_ENABLED=true
LLM_API_KEY=...   # live 全链路需要

docker compose up -d qdrant   # RAG 检索可选
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000
```

预置配置（Gateway 启动自动加载）：

- `config/agents.yaml` → `rag_specialist`
- `config/orchestrator_workflows.yaml` → `agent-vertical-rag`

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'lineColor': '#8b949e'}}}%%
sequenceDiagram
  participant U as 用户/演示脚本
  participant O as Orchestrator
  participant R as rag_specialist
  participant A as Agent Run
  participant H as HITL
  participant AUD as Audit

  U->>O: execute agent-vertical-rag
  O->>R: agent_call 委托
  R-->>O: output
  U->>A: /v1/agent/run httpbin_delay
  A->>H: pending approval_id
  U->>H: confirm
  U->>A: resume approval_id
  A->>AUD: action_level + approval_id
```

---

## 第 1 段：Orchestrator + Multi-Agent

```bash
export BASE=http://127.0.0.1:8000
export HDR=(-H "X-Tenant-Id: admin" -H "Authorization: Bearer sk-tenant-admin-change-me")

# 确认预置 Agent
curl -sf "$BASE/internal/agents" "${HDR[@]}" | python3 -c "import json,sys; print([a['agent_id'] for a in json.load(sys.stdin)])"

# 确认预置工作流
curl -sf "$BASE/internal/orchestrator/workflows" "${HDR[@]}" | python3 -c "import json,sys; print([w['workflow_id'] for w in json.load(sys.stdin)])"

# 执行 vertical 工作流（需 LLM_API_KEY）
curl -sf -X POST "$BASE/internal/orchestrator/workflows/agent-vertical-rag/execute" \
  "${HDR[@]}" -H "Content-Type: application/json" \
  -d '{"inputs": {"query": "RAG 数据管道"}}' | python3 -m json.tool
```

---

## 第 2 段：HITL 治理链（Agent Run）

```bash
SESSION=vertical-demo-$(date +%s)

# 1. 触发高风险工具 → 202 pending
RESP=$(curl -s -X POST "$BASE/v1/agent/run" \
  "${HDR[@]}" -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"admin\",
    \"session_id\": \"$SESSION\",
    \"messages\": [{\"role\": \"user\", \"content\": \"请调用 httpbin_delay，seconds=1，用于治理演示\"}]
  }")
echo "$RESP" | python3 -m json.tool
APPROVAL_ID=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('approval_id',''))")

# 2. 人工确认
curl -sf -X POST "$BASE/internal/agent/approvals/$APPROVAL_ID/confirm" "${HDR[@]}"

# 3. 续跑
curl -sf -X POST "$BASE/v1/agent/run" \
  "${HDR[@]}" -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"admin\",
    \"session_id\": \"$SESSION\",
    \"approval_id\": \"$APPROVAL_ID\",
    \"messages\": []
  }" | python3 -m json.tool
```

---

## 第 3 段：审计验证

```bash
# 工具分级
curl -sf -X POST "$BASE/internal/audit-actions/classify" \
  "${HDR[@]}" -H "Content-Type: application/json" \
  -d '{"tool_name": "httpbin_delay", "arguments": {"seconds": 1}}'

# 审计记录（应含 approval_id + action_level=network）
curl -sf "$BASE/internal/audit-actions/actions?tenant_id=admin&action_level=network&limit=10" \
  "${HDR[@]}" | python3 -m json.tool
```

---

## 一键 Smoke

```bash
# 无 Key：HITL 状态机 + 预置配置 + audit classify
python eval/agent_vertical_smoke.py

# 有 Key：含 orchestrator execute + HITL live 链
python eval/agent_vertical_smoke.py --with-llm

# 或接入 acceptance_smoke
python eval/acceptance_smoke.py --agent-vertical --with-llm
```

---

## 面试讲法（30 秒）

「我们有一条 vertical：编排层把检索委托给 `rag_specialist`；执行层对 `httpbin_delay` 这类网络工具走 HITL，审批后才续跑；每次 pending/执行都会写入 audit，带 `action_level` 和 `approval_id`，可追溯到具体审批单。」
