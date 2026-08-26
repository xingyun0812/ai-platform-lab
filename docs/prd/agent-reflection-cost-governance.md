## Problem Statement

Agent 系统的反思（self-refine / self-evolve）机制能显著提升输出质量，但会成倍放大 Token 成本与时延。当前项目反思的触发是**无差别的**：`self_refine` 迭代（默认 max_iterations=5）、`trigger_self_evolve`（每 run 反思 + 策略 patch）对每个任务一视同仁地开跑，缺少成本治理。生产环境的真实约束是「智能纠错能力」与「性能/成本」必须平衡——但现状缺乏四个核心能力：

1. **无反思深度分级**：没有 full/light/off 的分层，低价值任务也付出与高价值任务同等的反思成本
2. **无异常触发判定**：反思只在任务跑完整的后置环节触发，任务进行中已连续失败时不主动进入纠错；正常合规任务也被强制反思
3. **无主动收敛判停**：反思依赖固定迭代上限（撞 `max_iterations` 才停），结果已收敛/不再改善时不会提前停止
4. **无成本收益度量**：反思消耗了多少 token/时延、带来了多少收益（错误率下降/返工减少）无量化，无法按 ROI 决定要不要开反思

## Solution

在现有 `self_refine`（迭代生成-反馈）、`self_evolve`（反思→策略 patch + HITL）、`guardrails/convergence.py`（收敛判停共享组件）、token 预算（`context_token_budget` / `token_budget_daily/monthly`）之上，补一个**统一反思网关 ReflectionGate**，对全项目所有会调 LLM 的反思/纠错链路做集中管控。核心原则：

1. **分级触发**：按配置声明的 `reflection_depth`（full / light / off）分层，不同价值任务付出不同反思成本
2. **异常阈值触发**：以「失败 / 失真 / 差评 / 连续失败」等异常事件作为反思触发信号，正常合规任务跳过迭代
3. **虚实异步解耦**：实时执行链路仅做「轻量即时校验」（light，同步、小模型 one-pass）；完整复盘、案例沉淀、策略优化异步后台执行（full，不阻塞响应时延）
4. **模型分层降本**：轻量校验/复盘用低成本小模型；大模型只承担核心业务推理与低置信度复核
5. **主动收敛判停 + 硬上限兜底**：复用 `guardrails/convergence.py`，结果不再改善即提前停止；`max_iterations` + `max_total_llm_calls` + 累计时延硬超时兜底
6. **ROI 可观测**：反思入口/出口记 token 消耗、时延、触发原因、收敛轮数，联动下游错误率/返工指标算收益

## User Stories

1. As a 平台开发者, I want 每个任务/工具通过配置声明 `reflection_depth: full|light|off`, so that 不同价值任务自动采用不同反思深度
2. As a 平台开发者, I want 未声明 `reflection_depth` 的链路有默认值（轻量兜底）, so that 现有 Agent 完全不受影响、向后兼容
3. As a 平台运维, I want 反射按任务优先级（full）开启多轮反思, so that 高精度核心业务获得最强纠错能力
4. As a 平台运维, I want 普通任务仅 single-pass 轻量校验（light）, so that 不牺牲大多数任务响应的时延
5. As a 平台运维, I want 低价值任务直接关闭反思（off）, so that 彻底杜绝无谓 Token 消耗
6. As a 平台开发者, I want 反思仅在任务失败/结果失真/连续失败等异常事件时触发, so that 正常合规任务跳过迭代流程
7. As a 平台开发者, I want 任务进行中连续多次失败时「前瞻性」触发反思, so that 不是等到任务整体失败后才回看纠错
8. As a 平台开发者, I want 实时执行链路仅做同步轻量即时校验, so that 本次任务的纠错即时生效
9. As a 平台开发者, I want 完整复盘、案例沉淀、策略优化异步后台执行, so that 不阻塞实时响应时延
10. As a 平台开发者, I want 轻量校验/复盘使用配置的低成本小模型, so that 大幅降低反思 Token 消耗
11. As a 平台开发者, I want 小模型裁定带置信度闸门, so that 低置信度时自动升级到大模型复核或 fail-open，防误判漏报
12. As a 平台开发者, I want 反思在结果不再改善时提前停止（复用 convergence）, so that 不必撞到固定迭代上限才停
13. As a 平台开发者, I want 反思轮次受 `max_iterations` + `max_total_llm_calls` + 累计时延硬超时三重兜底, so that 极端波动下也不发生成本爆炸
14. As a 平台开发者, I want 按错误模式 hash 去重同一失败触发, so that 相同失败模式不反复付反思成本
15. As a 平台运维, I want 反思的 token 消耗/时延/触发原因/收敛轮数记入 perf_metrics, so that 能做成本收益 ROI 度量
16. As a 平台运维, I want 反思收益（错误率↓/返工↓）与成本联动可观测, so that 能按 ROI 决定是否开启/调深度
17. As a 平台开发者, I want ReflectionGate 对所有反思链路统一前置拦截, so that 单一入口即可管控分级/成本，无需逐链路改
18. As a 平台开发者, I want 分级阈值/模型/超时全部 config 化, so that 阈值不硬编码、可调优
19. As a 平台运维, I want 各分级触发次数、收敛轮数、ROI 有监控指标, so that 分级策略可迭代优化
20. As a 平台开发者, I want 反思网关是独立可测试模块, so that 不依赖 LLM/数据库即可单测验证

## Implementation Decisions

### Modules to build/modify

**新模块：**

| 模块 | 功能 | 测试隔离性 |
|------|------|-----------|
| `reflection_gate.py` | 反思统一网关：按 `reflection_depth` 决策是否放行/降级/拦截任何反思调用 | 纯决策逻辑，可注入 mock |
| `reflection_policy.py` | 反思深度分级判定 + 配置加载（full/light/off + 默认回退） | 纯配置，零依赖 |
| `reflection_metrics.py` | 反思成本/收益指标收集（token、时延、触发原因、收敛轮数、ROI） | 纯函数 + mock store |
| `tests/test_reflection_gate.py` | 网关放行/降级/拦截/默认回退单测 | 零外部依赖 |
| `tests/test_reflection_policy.py` | 分级判定 + 配置解析单测 | 零外部依赖 |

**修改模块：**

| 模块 | 改动 |
|------|------|
| `self_refine/orchestrator.py` | 生成/反馈/修正每次 LLM 调用前经 ReflectionGate；`full` 保留现有收敛，`light` 单轮，`off` 直通 |
| `self_refine/config.py` | 增加 `reflection_depth` + 小模型配置 + 置信度闸门配置 + 时延硬超时 |
| `self_evolve.py` | `trigger_self_evolve` 前经 ReflectionGate；`off` 完全跳过、`light` 只存经验不 LLM 反思、`full` 全流程 |
| `guardrails/convergence.py` | 复用为主（已抽取共享组件），必要时增加置信度返回 |
| `perf_metrics.py` | 增加反思指标：`record_reflection_*`（token、时延、触发原因、收敛轮数、分级） |
| `react_loop.py` / `runner.py` | 反思相关调用点接入 ReflectionGate（通过 policy 门控，默认透传保兼容） |

### Technical decisions

- **分级判定来源**：配置声明。每个任务/工具显式或默认 `reflection_depth`，无默认回退到 light（fail-safe，不破坏实时链路）
- **同步 vs 异步边界**：`light` 即时校验走同步小模型 one-pass；`full` 的完整复盘/沉淀/策略 patch 走 fire-and-forget 异步，不阻塞响应
- **收敛判停**：复用 `guardrails/convergence.py` 的 `check_convergence()`（similarity / llm_judged / hybrid），不自建新收敛模块
- **三重兜底**：`max_iterations`（现有默认 5）+ `max_total_llm_calls`（现有默认 15 硬上限 30）+ 新增累计时延硬超时 `max_total_latency_s`
- **模型分层**：反思链路默认走配置的 fast-llm（小模型）；大模型仅用于核心推理 + 低置信度复核
- **置信度闸门**：小模型裁定返回置信度，低于阈值 → 升级大模型复核；复核失败/超时 → fail-open
- **去重**：以错误模式 SHA256 hash 作为反思触发键，命中缓存直接跳过
- **全部向后兼容**：ReflectionGate 默认放行（depth=legacy 时等同现状），现有 Agent 与调用不破坏；所有新旧配置并存

### Config changes

新增 `reflection` 配置段（YAML）：`default_depth`、每级的 `model` / `max_iterations` / `max_total_llm_calls` / `max_total_latency_s`、`confidence_threshold`、`dedup_enabled`、`async_offload`（哪些深度异步）。

### Execution flow

```
任意反思点 (self_refine / self_evolve / 即时校验)
  → ReflectionGate.decide(depth, trigger_event, error_signature)
    → off             → 直通返回，零 LLM
    → light (sync)    → 小模型 one-pass 即时校验 → 置信度闸门 → 达标返回/低置信度升级
    → full            → 进入迭代反思：
                          loop: 校验 → 反馈 → check_convergence() → 收敛即 break
                          (受 max_iterations / max_total_llm_calls / max_total_latency_s 三重兜底)
    → dedup: 命中 error_signature hash → 跳过
  → reflection_metrics.record(reason, depth, tokens, latency, rounds, cost, outcome)
```

### Prior art / reuse notes

- `self_refine/orchestrator.py` + `guardrails/convergence.py`：迭代 + 收敛判停主流程，已存在
- `self_evolve.py`：反思→策略 patch → HITL，已存在（含每日 patch 上限）
- `perf_metrics.py`：指标收集骨架，已有 `record_self_evolve_*` 可扩展
- token 预算：`context_token_budget` / `token_budget_daily/monthly` 已有，反思成本纳入预算计数

## Testing Decisions

### Good tests

- 只测外部行为（给定任务深度/异常事件 → 判定放行/降级/拦截），不测内部实现细节
- 不依赖 LLM：mock `forward_with_model_router` 与 convergence
- 不依赖数据库：ReflectionGate / policy / metrics 用纯逻辑 + InMemory 可测
- 收敛/兜底：验证「已收敛提前停」「撞上限停」「超时停」三种终止路径

### Test modules

| Test file | Coverage |
|-----------|----------|
| `tests/test_reflection_policy.py` | 分级判定、默认回退、配置解析、非法深度报错 |
| `tests/test_reflection_gate.py` | off 直通零 LLM / light 单轮 / full 多轮收敛即停 / dedup 跳过 / 置信度升级 |
| `tests/test_reflection_metrics.py` | token/时延/触发原因/收敛轮数/ROI 记录与聚合 |

### Prior art

- `tests/test_state_machine.py` — 纯逻辑无依赖测试风格
- `tests/test_guardrails.py` / `convergence` 相关测试 — mock callback 验证行为
- `tests/test_long_horizon_persistence.py` — store 层 InMemory 双后端模式

## Out of Scope

- 大规模真实压测验证反思对 P99 时延的实际影响
- 自动调节分级深度（RL / 在线调参）— 本期仅 config 化 + 观测，调节靠人工
- 反思成本的 UI 看板（Console 展示）
- 改变现有 `self_refine` 对外 API 契约（仅网关前置，不重写接口）
- 全链路分布式链路追踪（已有观测能力，不新增 trace 平台）

## Further Notes

- 建议实现顺序：策略/网关/指标三个纯模块 → 单测 → 接入 `self_refine` → 接入 `self_evolve` → config 接线 → ROI 观测
- `guardrails/convergence.py` 已从 self_refine 抽取为共享组件，是本能力复用的关键，勿另起炉灶
- 成本治理的本质是 ROI——先有指标，才谈得上调深度
- 对应 ADR：建议新增 `docs/adr/0010-reflection-cost-governance.md`（非平凡架构决策）