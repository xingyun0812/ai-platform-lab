# Phase O #93 — 数据分析 Vertical

> **Issue**：[#93](https://github.com/xingyun0812/ai-platform-lab/issues/93)  
> **状态**：✅ 已交付

## 场景

分析 lab 演示销售表：**web_search（背景）→ sql_query（聚合）→ calc（同比）→ 报告摘要**。

## 配置

| 资产 | 路径 |
|------|------|
| Orchestrator workflow | [`config/workflows/data_analysis.yaml`](../config/workflows/data_analysis.yaml) |
| Agent spec | `data_analyst` in [`config/agents.yaml`](../config/agents.yaml) |
| SQL seed | [`samples/analytics_demo.sql`](../samples/analytics_demo.sql) |

Workflow ID：`data-analysis-vertical`

## 验证

```bash
# 离线 mock（无需 Gateway / LLM）
./eval/data_analysis_vertical.sh --mock

# Live：需 Gateway + LLM_API_KEY
./eval/data_analysis_vertical.sh --live
```

## Live 步骤

1. `docker compose up -d` 或 `uvicorn apps.gateway.main:app`
2. 设置 `LLM_API_KEY`（live 模式下 orchestrator HTTP execute 仍走 tool 链，通常无需 Key；`--live` 主要测 API 可达）
3. 运行 `--live` 检查 `/internal/orchestrator/workflows/data-analysis-vertical/execute`

## Console / 轨迹

- Orchestrator execute 响应含 `trace` 与 `final_output`（报告 Markdown 模板）
- Multi-Agent 委托 `data_analyst` 时可用 `session_id` 查 [`GET /v1/agent/blackboard/{session_id}`](../docs/phase-o-multi-agent-v2.md)

## 与 Phase L vertical 关系

| Vertical | ID | 链路 |
|----------|-----|------|
| Agent RAG (#59) | `agent-vertical-rag` | Multi-Agent 委托 |
| Data Analysis (#93) | `data-analysis-vertical` | tool_call 链 + 报告 |
