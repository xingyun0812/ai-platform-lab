# Phase S — Tree of Thoughts (ToT) 推理模式

> **状态**：✅ **已交付**（S1～S4 · Phase S）
> **前置**：Phase Q（Graph Runtime）· Phase R（Harness 基础设施）
> **门禁**：`python eval/tot_quality_gate.py run && python eval/tot_quality_gate.py gate --threshold 5`

---

## 1. 动机

项目已完成 Phase A~R，ReAct 循环与 CoT 推理模式已在生产使用。但遇到需要深度推理的任务时，单路径 CoT 存在以下局限：

1. **局部最优** — 单路径推理可能过早承诺一个错误方向
2. **无回溯机制** — 无法回退并尝试其他推理路径
3. **无系统性比较** — 不同推理策略之间缺乏显式的比较与评估

**Phase S 目标**：在不修改现有 ReAct Runtime 的前提下，引入 Tree of Thoughts 多路径推理，作为可选的增强层。

**非目标**：替换 CoT 或 ReAct；训练专用模型；在线 RL 推理。

---

## 2. 与现有模式的对比

| 维度 | ReAct | CoT | ToT |
|------|-------|-----|-----|
| 路径数 | 1 | 1 | N（树状） |
| 回溯 | 不支持 | 不支持 | BFS/DFS 搜索 |
| 评估 | 无 | 无 | 显式评分剪枝 |
| Token 消耗 | 低 | 中 | 高 |
| 适用场景 | 简单工具调用 | 中等推理 | 复杂多步推理 |

```
react:   LLM → tool → LLM → tool → ...
cot:     LLM <thinking> → tool → ...
tot:     树状探索 → LLM(候选1) + LLM(候选2) + ... → 评估 → 选择 → 继续
```

---

## 3. 架构

```
┌─────────────────────────────────────────────────────────────┐
│                   ToT 推理引擎                                 │
│                                                               │
│  Goal → ThoughtGenerator → [候选1, 候选2, ...]                 │
│           ↓                                                     │
│       ThoughtEvaluator → 评分 + 分类 (sure/maybe/impossible)    │
│           ↓                                                     │
│       TotSearcher (BFS/DFS) → 剪枝 → 下一轮                    │
│           ↓                                                     │
│       best_path() → 最优推理链 → 最终答案                       │
└─────────────────────────────────────────────────────────────┘
          ↓
  ┌────────────────┐
  │ 注入方式         │
  │ 1. POST /v1/agent/tot（独立 API）  │
  │ 2. auto_plan + tot_config（Plan 前置） │
  └────────────────┘
```

### 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/tot/__init__.py` | `run_tot()` 编排入口 |
| `packages/agent/tot/tree.py` | `ThoughtNode`, `ThoughtTree`, `TotConfig` 数据模型 |
| `packages/agent/tot/generator.py` | `ThoughtGenerator` — LLM 候选思维生成 |
| `packages/agent/tot/evaluator.py` | `ThoughtEvaluator` — 评分与分类 |
| `packages/agent/tot/searcher.py` | `TotSearcher` — BFS beam search + DFS 回溯 |
| `config/agent_tot.yaml` | ToT 策略配置 |
| `apps/gateway/agent/routes.py` | `POST /v1/agent/tot` API 路由 |
| `packages/agent/graph_runtime.py` | auto_plan 时 ToT 前置注入 |
| `eval/tot_quality_gate.py` | ToT vs CoT 基准评测 |
| `tests/test_tot.py` | 30 个单元测试 |
| `docs/adr/0004-tree-of-thoughts.md` | 架构决策记录 |

---

## 4. API

### 独立 ToT 推理

```bash
curl -s http://127.0.0.1:8000/v1/agent/tot \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "tot-demo",
    "goal": "计算 23 × 45",
    "tot_config": {
      "enabled": true,
      "search_algorithm": "bfs",
      "branching_factor": 3,
      "beam_width": 2,
      "max_depth": 5
    }
  }'
```

响应：

```json
{
  "final_message": "23 × 45 = 1035",
  "tot_result": {
    "best_answer": "23 × 45 = 1035",
    "best_value": 0.95,
    "total_nodes": 7,
    "search_depth": 3,
    "execution_time_ms": 2340.5,
    "tree": { ... }
  }
}
```

### auto_plan + ToT 前置推理

```bash
curl -s http://127.0.0.1:8000/v1/agent/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "plan-tot-demo",
    "auto_plan": true,
    "goal": "分析销售数据并生成报告",
    "tot_config": {
      "enabled": true,
      "search_algorithm": "bfs",
      "branching_factor": 2,
      "beam_width": 2,
      "max_depth": 3
    }
  }'
```

---

## 5. 配置

见 `config/agent_tot.yaml`：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `search_algorithm` | `bfs` | 搜索算法（bfs/dfs） |
| `branching_factor` | 3 | 每节点候选数 |
| `beam_width` | 2 | BFS 每层保留节点数 |
| `max_depth` | 5 | 最大搜索深度 |
| `max_total_nodes` | 50 | 总节点上限 |
| `value_threshold` | null | DFS 剪枝阈值 |
| `temperature` | 0.7 | 生成温度 |
| `timeout_seconds` | 120 | 超时 |

---

## 6. Eval 门禁

```bash
# 运行 ToT vs CoT 基准对比
python eval/tot_quality_gate.py run --sample-limit 10

# 门禁检查（ToT 不低于 CoT 超过 5%）
python eval/tot_quality_gate.py gate --threshold 5
```

---

## 7. 与 Phase T/U 的关系

Phase S 是高级推理能力的第一阶段，后续两个阶段独立演进：

- **Phase T**: Multi-Agent Debate — 多 Agent 围绕同一问题独立推理、互相评议
- **Phase U**: Deep Research — 问题分解 + 迭代搜索 + 信息综合

ToT 的树搜索基础设施（BFS/DFS、评分、剪枝）可直接被 Debate/Research 复用。

---

## 8. 已知限制

| 限制 | 说明 | 后续改进方向 |
|------|------|-------------|
| Token 消耗高 | 每节点多次 LLM 调用 | 加入缓存 / 批量评估 |
| 评分依赖 LLM | Evaluator 本身也是 LLM 调用 | 可考虑数学规则评分 |
| 无持久化 | 搜索树纯内存 | Phase U 时考虑 checkpoint |
| MCTS 未实现 | `visits` 字段已预留 | 独立 PR 加入 |
