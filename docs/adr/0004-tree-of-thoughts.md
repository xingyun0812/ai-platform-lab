# ADR-0004: Tree of Thoughts (ToT) 推理模式

- **Status**: accepted
- **Date**: 2026-08-05
- **Issue**: — (Phase S)
- **Tags**: phase-s, agent, tot, reasoning

## Context

项目已完成 Phase A~R，ReAct 循环与 CoT 推理模式已在生产使用。但遇到「需要深度推理」的任务时，单路径 CoT 无法探索多条推理路径，容易陷入局部最优。

业界常见方案：
| 方案 | 复杂度 | 与现有架构的耦合 |
|------|--------|------------------|
| ToT (Tree of Thoughts) | 中 | 低（可选模式） |
| Multi-Agent Debate | 高 | 中（复用 delegation） |
| MCTS + LLM | 高 | 中 |

**为什么选择 ToT 作为第一期**：
1. 算法相对成熟，实现边界清晰
2. 不修改现有 ReAct 流程，以可选模式存在
3. 为 Debate/Research 提供树搜索基础设施

## Decision

### 1. ToT 作为可选推理模式

ToT 不与现有 `planner.py` 竞争，而是作为 **可插拔的推理增强层**：

```
无 ToT:  user → ReAct / CoT → output
有 ToT:  user → ToT 搜索 → 最优推理链 → ReAct 执行 → output
```

三种启用方式：
1. **独立 API**: `POST /v1/agent/tot` — 纯 ToT 推理
2. **推理模式**: `reasoning_mode: "tot"` — 嵌入现有 Agent 流程
3. **Plan 增强**: `auto_plan: true` + `tot_enabled: true` — ToT 产出注入 Planner

### 2. 搜索算法选择

| 维度 | 决策 |
|------|------|
| 默认算法 | BFS + beam search（宽度优先，最稳定） |
| 可选算法 | DFS + 回溯（深度优先，早期剪枝） |
| 预留 | MCTS 字段（`visits`）已定义，后续可加 |
| 剪枝策略 | Evaluator 的 `status` 字段（sure/maybe/impossible） |

### 3. 目录结构

```
packages/agent/tot/
  __init__.py     # run_tot() 编排入口
  tree.py         # 数据模型
  generator.py    # 候选思维生成
  evaluator.py    # 思维评分
  searcher.py     # BFS/DFS 搜索
```

### 4. observability

- 搜索树结构通过 `TotResult.trace` 暴露
- 每个步骤的延迟和 token 消耗通过 `perf_metrics.py` 记录
- 后续可接入 OpenTelemetry span（复用 `component_span`）

## Consequences

### Positive

- ToT 完全可选，不干扰现有 ReAct/CoT 流程
- 树搜索基础设施可被后续 Debate/Research 复用
- 所有算法参数通过 YAML 配置，运行时无需改代码
- 清晰的 eval 门禁可量化 ToT 相对于 CoT 的提升

### Negative / trade-offs

- ToT 的 token 消耗显著高于单路径 CoT（branching_factor × depth 倍）
- 不适合简单问答（延迟增加但无收益）
- BFS beam search 可能剪掉反直觉的正确路径

### Follow-up

- [ ] Phase T: Multi-Agent Debate（复用树框架，但以 Agent 为单位）
- [ ] Phase U: Deep Research（问题分解 + 迭代搜索）

## Alternatives considered

| 方案 | 为何未选 |
|------|----------|
| MCTS + LLM | 实现复杂度高，需要价值网络或 rollout 策略。预留字段后续扩展 |
| 直接调 API structured output | 只解决输出格式不解决多路径探索 |
| LangGraph 集成 | 引入外部依赖，且与现有架构耦合过重 |

## References

- `docs/02-phase-q-00-advanced-planning.md` — Phase Q 明确将 ToT 列为非目标，现已进入 scoped Phase S
- `packages/agent/reasoning.py` — 现有 CoT 模式，ToT 在其基础上扩展
- `packages/agent/planner.py` — 现有 Planner，ToT 可选前置注入
- Wei et al., "Tree of Thoughts: Deliberate Problem Solving with Large Language Models" (NeurIPS 2023)
