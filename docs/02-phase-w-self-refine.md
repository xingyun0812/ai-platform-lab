# Phase W — Self-Refine（自我修正推理）

> **状态**：🔄 实现中（Phase W · {{CURRENT_DATE}}）
> **Issue**：[#204](https://github.com/xingyun0812/ai-platform-lab/issues/204)
> **ADR**：[0008-self-refine.md](./adr/0008-self-refine.md)
> **门禁**：`python eval/self_refine_quality_gate.py run && gate`

## 概述

Self-Refine（Madaan et al., 2023）是一种单 Agent 自我迭代推理模式：

```
初始输出 → 自我反馈 → 自我修正 → 收敛检查 →（未收敛）→ 继续反馈
                                            →（收敛）→ 返回最终输出
```

与项目中已有的高级推理模式形成互补：

| 模式 | 特点 | Phase |
|------|------|-------|
| CoT | 链式思考 | Phase O |
| ToT | 树搜索 + BFS/DFS | Phase S |
| Debate | 多角色辩论 | Phase T |
| Deep Research | 搜索-阅读-综合 | Phase U |
| Computer Use | GUI 操作 | Phase V |
| **Self-Refine** | **单 Agent 自我迭代修正** | **Phase W** |

## 核心设计

### 配置参数（SelfRefineConfig）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_iterations` | 5 (上限 10) | 最大迭代轮数 |
| `generator_model` | None | 生成器模型，可分离 |
| `feedback_model` | None | 反馈器模型，None 时复用 generator_model |
| `convergence_strategy` | "hybrid" | 收敛策略：llm_judged / similarity / hybrid |
| `convergence_threshold` | 0.85 | similarity 模式阈值 |
| `max_total_llm_calls` | 15 (上限 30) | 单次请求 LLM 调用硬上限 |
| `feedback_dimensions` | 5 维度 | 结构化反馈维度列表 |
| `temperature` | 0.3 | LLM 生成温度 |
| `timeout_seconds` | 120.0 | 超时截断 |

### 收敛策略

1. **llm_judged**：LLM 接收「当前输出 + 上一轮反馈」判断是否还有新改进点
2. **similarity**：当前轮与上一轮输出语义相似度 >= threshold 即收敛（依赖 Embedding 服务）
3. **hybrid**（sequential AND）：similarity 快速检查 → 若 >= threshold 则收敛（跳过 LLM judge）→ 否则 LLM judge 确认

### 结构化反馈维度

- `correctness`：事实正确性、逻辑漏洞
- `clarity`：表达清晰度、歧义
- `completeness`：是否遗漏关键信息
- `consistency`：内部一致性、与 prompt 要求一致
- `actionability`：输出是否可执行（针对代码/JSON）

### 错误处理

- `feedback()` 失败 → 重试 1 次 → 降级返回空反馈 → 不断送整次请求
- `refine()` 失败 → 重试 1 次 → 保留上一轮输出继续
- 超时 → 截断返回当前最佳结果
- LLM 调用数达到硬上限 → 截断

## API

```
POST /v1/agent/self-refine
Content-Type: application/json
X-Tenant-Id: <tenant_id>
Authorization: Bearer <token>

{
  "tenant_id": "...",
  "session_id": "...",
  "goal": "要解决的问题",
  "model": "可选模型名",
  "self_refine_config": {
    "max_iterations": 5,
    "generator_model": null,
    "feedback_model": null,
    "convergence_strategy": "hybrid",
    "max_total_llm_calls": 15
  }
}
```

响应包含迭代轨迹（每轮的 feedback + refine 结果）。

## 文件结构

```
packages/agent/self_refine/
├── __init__.py        # 公开 API
├── config.py          # SelfRefineConfig dataclass
├── models.py          # FeedbackRound / SelfRefineResult
└── orchestrator.py    # 核心循环编排
```

## 已知限制

1. **上下文增长**：每轮 refinement 将「当前输出 + 历史反馈」追加到 prompt 中，5 轮后 context 线性膨胀。极端情况可能触及 token 限制。未来优化：只传增量 diff。
2. **Similarity 策略依赖 Embedding 服务**：需要 Phase P 的 Embedding 服务可用，否则回退到字符串精确匹配。
3. **统计门禁脆弱性**：30 题的 benchmark margin of error ~+/-9%，门禁采用 no-regression（而非固定百分比）避免 flake。

## 与自进化 Agent（Phase R）的区别

| 维度 | Self-Refine (Phase W) | 自进化 Agent (Phase R) |
|------|----------------------|----------------------|
| 范围 | 单次请求内 | 跨 session |
| 持久化 | 无 | 经验库 + 策略补丁 |
| 反馈 | LLM 自我反馈 | LLM 反思 + HITL 审批 |
| 输出 | 修正后的输出 | 策略补丁注入未来规划 |
