# ADR-0008: Self-Refine 推理模式

## 状态

提议中

## 上下文

项目已交付 ToT（树搜索）、Debate（多角色辩论）、Deep Research（搜索-阅读-综合）、Computer Use（GUI 操作）四种高级推理模式。缺少「单 Agent 自我迭代修正」路径。Madaan et al. 2023 的 Self-Refine 范式填补这一空白。

## 决策

1. **遵循已有高级推理模式惯例**：dataclass Config/Result → Pydantic Schemas → routes.py 注册 → 质量门禁，与 ToT/Debate/Research 一致
2. **模型可分离**：支持 `generator_model` / `feedback_model` 分开指定，参考 Debate 的 proposer_model / critic_model / judge_model 模式
3. **三路收敛策略**：llm_judged / similarity / hybrid（sequential AND），hybrid 为默认
4. **硬调用上限**：`max_total_llm_calls` 默认 15，上限 30，防止费用失控
5. **路由合并**：Self-Refine 路由合并到 `apps/gateway/agent/routes.py`，不创建独立 router 文件
6. **命名区分**：明确与 Phase R 的 `self_evolve.py` 区分——Self-Refine 是单次请求内的迭代修正，不涉及跨 session 持久化

## 备选方案

- **复用自进化 Agent**：范围不同（跨 session vs 单次），不适用
- **独立 router 文件**：与 ToT/Debate/Research 惯例不一致，增加维护成本
- **仅 llm_judged 收敛**：缺少 cost-saving 路径，hybrid 提供 similarity 快速通道

## 影响

- 正向：填补推理模式拼图，面试可对标 SOTA 论文
- 负向：每轮 3-4 次 LLM 调用，需要 `max_total_llm_calls` 硬上限防护
- 风险：LLM 自我反馈存在 self-blindness（无法发现自身错误），通过模型分离和结构化维度缓解

## 后续

- 增量 diff context 压缩（减少 token 膨胀）
- 与现有 Agent ReAct 循环集成（通过 reasoning_mode 触发）
