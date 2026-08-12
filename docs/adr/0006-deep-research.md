# ADR-0006: Deep Research 推理模式

- **Status**: accepted
- **Date**: 2026-08-05
- **Tags**: phase-u, agent, research, deep-research

## Context

Phase S（ToT）和 Phase T（Debate）已交付，两者都需要「给定上下文后再推理」。但实际研究场景中，Agent 需要**自主获取外部信息**：搜索网络、阅读网页、综合信息、迭代深入。

已有基础设施：
- `web_search` tool — 搜索网络
- `fetch_url` tool — 获取网页全文（Phase U 新增）
- `forward_with_model_router()` — LLM 调用
- ToT/Debate 的 `run_*()` 编排模式

## Decision

### 1. Deep Research 流程

1. **问题分解** — LLM 将研究问题分解为 N 个可搜索的子问题
2. **搜索+阅读** — 对每个子问题执行 web_search → fetch_url → LLM 摘要
3. **信息综合** — 将所有 ResearchNote 综合为结构化 Markdown 报告

### 2. 复用已有设施

- `web_search` tool — 搜索结果
- `fetch_url` tool — 网页正文提取
- `forward_with_model_router` — 所有 LLM 调用

### 3. 优雅降级

- 问题分解失败 → 将原问题作为唯一子问题
- 单个搜索/阅读失败 → 跳过该来源
- 综合失败 → 简单拼接

## Consequences

### Positive

- 完全复用现有工具和编排模式
- 可独立调用或集成到 Agent 流程
- 报告为 Markdown 格式，可直接展示

### Negative

- Token 消耗高（每次 fetch_url + 每次摘要 = 多次 LLM 调用）
- 依赖外部搜索服务的可用性
- 无持久化缓存（后续可加）

## Alternatives

| 方案 | 为何未选 |
|------|----------|
| ReAct 风格搜索循环 | 缺乏问题分解和综合的结构化输出 |
| 单次搜索+回答 | 无法覆盖多维度研究问题 |

## References

- `packages/agent/research/__init__.py` — `run_research()` 编排器
- Shao et al., "STORM: Synthesis of Topic Outlines through Retrieval and Multi-perspective Question Asking"
