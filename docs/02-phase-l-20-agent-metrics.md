# Phase L #58 — Agent 三率 + Precision@1

> **状态**：✅ 四率指标 + `agent_metrics` 报告块

## 指标定义

| 指标 | 分母 | 分子 |
|------|------|------|
| **Tool Precision@1** | 有 `expect_tools` 且第一步可判定 | 第一步工具 ∈ `expect_tools` |
| **Needless Tool Rate** | `direct_answer: true`（兼容 `expect_no_tools`） | 实际调用了工具 |
| **Missing Tool Rate** | `require_tools: true`（兼容非空 `expect_tools`） | 未调用任何工具 |
| **Arg Valid Rate** | 有 tool_calls 的用例 | error 不含 `AGENT_TOOL_BAD_ARGS` |

## Baseline 字段

`eval/agent_baseline.jsonl` / `eval/baselines/agent_scenarios.jsonl`：

```json
{
  "direct_answer": true,
  "require_tools": true,
  "expect_tools": ["calc"],
  "forbid_tools": ["httpbin_delay"]
}
```

## 命令

```bash
# 校验 baseline（无需 Gateway）
python eval/agent_run.py validate-baseline

# 跑轨迹评测（需 Gateway + LLM_API_KEY）
python eval/agent_run.py run --base-url http://127.0.0.1:8000

# 对比两次 run 的四率
python eval/agent_run.py compare eval/runs/agent/run-a.json eval/runs/agent/run-b.json
```

报告含 `agent_metrics` 块：

```json
{
  "tool_precision_at_1": 1.0,
  "needless_tool_rate": 0.0,
  "missing_tool_rate": 0.0,
  "arg_valid_rate": 1.0
}
```

## 测试

```bash
python -m pytest tests/test_agent_metrics.py -q
```
