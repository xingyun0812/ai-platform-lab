# ADR-0010: 反思成本治理——统一反思网关 ReflectionGate

- **Status**: accepted
- **Date**: 2026-08-26
- **Issue**: #256
- **Deciders**: xingyun0812
- **Tags**: phase-r, agent, reflection, cost, adr

## Context

Agent 的反思机制（`self_refine` / `self_evolve`）能显著提升输出质量，但会成倍放大 Token 成本与时延。当前反思触发是**无差别的**：每个任务走同样的迭代深度，缺少成本治理，生产环境在「智能纠错能力」与「性能/成本」之间失衡。开工前有两个必须拍板的设计决策：

1. **管控纵深**：反思成本治理应落在哪一层。候选 —— (a) 统一反思网关 `ReflectionGate`，集中管控全项目所有反思链路；(b) 仅增强 `self_refine` 底座；(c) 仅做异步复盘闭环。这决定了改造范围与收益上限。
2. **分级判定来源与收敛判停**：反思深度（full/light/off）由谁决定；迭代是否引入主动收敛判停（结果不再改善即停），还是只保留固定上限兜底。这决定了成本治理的精细度与复用的深度。

## Decision

### 决策 1：统一反思网关 `ReflectionGate`（纵深 = 全项目集中管控）

新增单一前置网关 `ReflectionGate`，对**所有会调 LLM 的反思/纠错链路**（`self_refine` 迭代、`self_evolve` 反思→策略 patch、运行期即时校验）做集中决策——放行 / 降级 / 拦截。不逐链路零散打补丁，单一入口即可管控分级与成本。`reflection_policy.py` 负责分级判定与配置加载。

### 决策 2：配置声明分级，复用现有收敛组件

- **分级来源** = **配置声明**。每个任务/工具显式或默认声明 `reflection_depth: full | light | off`，未声明回退 `light`（fail-safe，不破坏实时链路）。不做运行期动态判定（难控、难审计）。
- **收敛判停** = **复用**已抽取的共享组件 `packages/agent/guardrails/convergence.py` 的 `check_convergence()`（similarity / llm_judged / hybrid），**不新建独立收敛模块**。兜底三重：`max_iterations` + `max_total_llm_calls` + 新增累计时延硬超时 `max_total_latency_s`。
- 模型分层降本：反思默认走低成本小模型；大模型仅承担核心推理与低置信度复核。小模型裁定配**置信度闸门**：低于阈值升级大模型复核，复核失败/超时 fail-open，防误判漏报。
- 按错误模式 SHA256 hash 去重触发，相同失败不反复付反思成本。
- **全部向后兼容**：网关默认放行（depth=legacy 时等同现状），现有 Agent 与调用不破坏，新旧配置并存。

### 虚实解耦

- 实时执行链路（`light`）：仅同步小模型 one-pass 即时校验，本次任务纠错即时生效。
- 深层链路（`full`）：完整复盘、案例沉淀、策略 patch 异步后台执行，不阻塞实时响应时延。

## Consequences

### Positive

- 单一入口集中治理分级/成本/ROI，改造可控、可观测。
- 复用 `guardrails/convergence.py`（已抽取共享组件），收敛判停不自建，跨链路一致。
- 向后兼容：默认回退 light / legacy 透传，不破坏现有 Agent。
- 成本/收益可量化（`perf_metrics.py` 记 token/时延/收敛轮数 + 联动错误率），按 ROI 调深度而非拍脑袋。

### Negative / trade-offs

- 配置声明分级需要调用点接入，新增 config 段与维护成本。
- 异步复盘结果只作用下游/后续任务，不参与当前任务纠错——实时性换取成本。
- 小模型置信度闸门引入额外一次大模型复核的偶发开销。

### Follow-up

- [ ] #256 在 `reflection_gate.py` / `reflection_policy.py` / `reflection_metrics.py` 落地本 ADR 的分级默认值与置信度闸门语义
- [ ] #256 接入 `self_refine` 与 `self_evolve`，新增 `reflection` config 段
- [ ] 同步 PRD `docs/prd/agent-reflection-cost-governance.md` 的 Implementation Decisions 与本文档字段语义保持一致