# Phase L #60 — Agent Baseline + CI 门禁

> **状态**：✅ 离线 gate + PR workflow

## 目标

- `agent_scenarios.jsonl` ≥ 30 条，覆盖 `require_tools` / `direct_answer`（三率分母）
- Agent 轨迹 **pass_rate + 四率** 相对 baseline 回退 >5% → CI block

## 文件

| 文件 | 说明 |
|------|------|
| `eval/baselines/agent_scenarios.jsonl` | Pipeline 批量用例（51 条） |
| `eval/agent_baseline.jsonl` | 轨迹 live 用例（5 条） |
| `eval/baselines/agent_gate_fixtures.jsonl` | 离线 mock 响应 |
| `eval/baselines/agent_trajectory_gate.json` | 轨迹 gate baseline |
| `eval/baselines/main_baseline.json` | RAG+Agent pipeline 总 baseline |
| `eval/agent_gate.py` | Agent 专用门禁 CLI |

## 命令

```bash
# 校验 scenarios 数量与三率字段
python eval/agent_gate.py validate

# 离线 gate（CI 无 Key，fixture 驱动）
python eval/agent_gate.py run-offline

# 对比两次 agent_run 报告
python eval/agent_gate.py check eval/runs/agent/run-a.json eval/runs/agent/run-b.json

# 轨迹 live 评测 + 通过率门禁
python eval/agent_run.py run --min-pass-rate 0.7
```

## CI（`.github/workflows/eval.yml`）

PR 到 `main` 时：

1. `python eval/agent_gate.py validate`
2. `python eval/agent_gate.py run-offline`
3. `pytest tests/test_agent_gate.py`

与 RAG `eval/run.py gate` 并行，互不替代。

## 门禁规则

```
delta_pp = (current_pass_rate - baseline_pass_rate) × 100
PASS  if delta_pp > -5.0
FAIL  if delta_pp ≤ -5.0
```

四率变化写入 `metric_deltas_pp` 供人工审阅，默认 **不单独 block**（与 RAG gate 一致，仅 pass_rate 硬门禁）。

## 测试

```bash
python -m pytest tests/test_agent_gate.py -q
```
