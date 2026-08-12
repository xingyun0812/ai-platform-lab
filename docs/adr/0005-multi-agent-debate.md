# ADR-0005: Multi-Agent Debate 推理模式

- **Status**: accepted
- **Date**: 2026-08-05
- **Tags**: phase-t, agent, debate, multi-agent

## Context

Phase S 交付了 ToT（Tree of Thoughts），但 ToT 的所有思维节点由**同一个 LLM** 生成和评估，缺乏多视角碰撞。对于需要权衡不同观点的事实性推理问题，单一模型的多路径搜索不如多 Agent 辩论有效。

已有基础设施：
- `multi_agent/delegation.py` — `delegate_to_agent()` 和 `parallel_delegate()`
- `multi_agent/blackboard.py` — 黑板存储和共享上下文
- `multi_agent/registry.py` + `config/agents.yaml` — Agent 角色定义

## Decision

### 1. 辩论流程

标准辩论分三轮（可配置）：
1. **提案轮**：N 个 Proposer Agent 并行独立推理
2. **评议轮**：N 个 Critic Agent 交叉评审提案
3. **裁定轮**：Judge Agent 基于全部提案和评议给出最终答案

### 2. 复用已有设施

- `parallel_delegate()` 并行发起辩论 Agent
- `BlackboardStore.append()` 记录每一轮输出
- `config/agents.yaml` 定义辩论角色
- 每个 Agent 独立 session，防止交叉污染

### 3. 优雅降级

- 单个 Proposer 失败不影响其他 Proposer
- 无 Critic 时直接跳过评议轮
- API/编排内部异常返回错误信息而非抛异常

## Consequences

### Positive

- 完全复用现有 multi-agent 基础设施
- 辩论角色可独立配置 model/temperature
- 可扩展：新增角色类型或辩论轮次

### Negative / trade-offs

- Token 消耗高（3 proposers + 3 critics + 1 judge = 7 次 LLM 调用）
- 延迟高于单 Agent（但所有并行调用同时执行）
- 对 Agent 注册表有依赖（需要配置 proposer/critic/judge 角色）

### Follow-up

- [ ] Phase U: Deep Research（问题分解 + 迭代搜索）

## Alternatives considered

| 方案 | 为何未选 |
|------|----------|
| 独立 API 调用模拟辩论 | 缺少黑板共享上下文和并行编排 |
| 单 Agent 多次推理 | 同模型缺乏多样性 |
| LangGraph 多节点编排 | 外部依赖，与现有架构耦合重 |

## References

- `packages/agent/debate/__init__.py` — `run_debate()` 编排器
- `packages/agent/multi_agent/delegation.py` — 并行委托
- `config/agents.yaml` — 辩论角色定义
- Du et al., "Improving Factuality and Reasoning in Language Models through Multiagent Debate" (ICML 2024)
