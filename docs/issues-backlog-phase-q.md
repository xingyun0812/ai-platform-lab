# Phase Q Issue Backlog — 任务规划前沿对齐

> 规划：[phase-q-advanced-planning.md](./phase-q-advanced-planning.md)  
> **Milestone**：Phase Q — Advanced Planning  
> **Tag**：`phase-q-advanced-planning`  
> **前置**：Phase O O1 Task Planner ✅（#87）

| Backlog | GitHub Issue | 状态 |
|---------|--------------|------|
| Q0 规划文档 | [#115](https://github.com/xingyun0812/ai-platform-lab/issues/115) | ✅ |
| Q1 Structured Plan | [#116](https://github.com/xingyun0812/ai-platform-lab/issues/116) | ✅ PR #123 |
| Q2 DAG 并行执行 | [#117](https://github.com/xingyun0812/ai-platform-lab/issues/117) | ✅ PR #122 |
| Q3 失败重规划 | [#118](https://github.com/xingyun0812/ai-platform-lab/issues/118) | ✅ PR #124 |
| Q4 Plan 级 HITL | [#119](https://github.com/xingyun0812/ai-platform-lab/issues/119) | ✅ PR #125 |
| Q5 Orchestrator 桥接 | [#120](https://github.com/xingyun0812/ai-platform-lab/issues/120) | 📋 |
| Q6 eval 门禁 + tag | [#121](https://github.com/xingyun0812/ai-platform-lab/issues/121) | 📋 |

---

## Q0 — 规划总览

**标题**：`[Phase Q] Advanced Planning — 任务规划前沿对齐规划`

**目标**：评审 `docs/phase-q-advanced-planning.md`，创建 Q1～Q6 Issue，更新 `roadmap.md`。

**验收**：
- [x] `docs/phase-q-advanced-planning.md` 评审通过
- [x] Milestone Phase Q 内 Q1～Q6 Issue 创建完毕（#116～#121）
- [x] `roadmap.md` 增加 Phase Q 章节

**非目标**：LangGraph 依赖绑定、ToT 搜索、亿级在线

---

## Q1 — Structured Plan 输出

**标题**：`[Phase Q] Q1 Structured plan output — json_schema / response_format`

**目标**：`generate_plan` 优先使用 upstream structured output；保留 JSON 解析降级。

**验收**：
- [ ] `packages/agent/planner.py` 支持 `PLAN_OUTPUT_MODE=structured|legacy`
- [ ] OpenAI 兼容 `response_format` 路径 + mock 单测
- [ ] `eval/agent_planner_smoke.py` 覆盖 structured 路径
- [ ] 单测 ≥8 新增或扩展

**依赖**：无 · **预估**：2～3d

---

## Q2 — DAG 并行 step 执行

**标题**：`[Phase Q] Q2 Parallel plan execution — DAG layers + asyncio`

**目标**：无依赖关系的 Plan step 同层并行 `run_agent`。

**验收**：
- [ ] `plan_execution_layers()` + `execute_plan_parallel()`
- [ ] session / blackboard 并发策略文档化 + 单测
- [ ] Prometheus `agent_plan_parallel_*` 指标
- [ ] `eval/agent_planner_smoke.py` 增并行用例

**依赖**：无（可与 Q1 并行 merge） · **预估**：3～4d

---

## Q3 — 失败重规划 Critic

**标题**：`[Phase Q] Q3 Replan on failure — critic loop + plan revisions`

**目标**：step 失败时 LLM critic 局部修订 Plan，限制 `max_replan_attempts`。

**验收**：
- [ ] `packages/agent/plan_critic.py`
- [ ] `execute_plan_*` 集成 replan；trace 含 `plan_revisions`
- [ ] 单测：失败 → replan → 成功
- [ ] Prompt `agent_plan_critic` in `config/prompts.yaml`

**依赖**：Q2 推荐 · **预估**：3～4d

---

## Q4 — Plan 级 HITL

**标题**：`[Phase Q] Q4 Plan-level HITL — approve plan before execute`

**目标**：`require_plan_approval` 时暂停在 Plan 审批；Console 展示步骤树。

**验收**：
- [ ] `AgentRunRequest.require_plan_approval` + approval action `approve_plan`
- [ ] `apps/gateway/agent/approval_routes.py` 扩展
- [ ] Console Plan 审批 UI（最小）
- [ ] 单测 + smoke

**依赖**：无 · **预估**：2～3d

---

## Q5 — Planner ↔ Orchestrator 桥接

**标题**：`[Phase Q] Q5 Plan to workflow bridge — export + adapter`

**目标**：Plan 导出为 workflow YAML 或与 Orchestrator 统一执行入口。

**验收**：
- [ ] `packages/agent/plan_workflow.py` — `plan_to_workflow()`
- [ ] `POST /v1/agent/plan/export` 或 CLI
- [ ] 与 `config/workflows/data_analysis.yaml` 字段对齐说明
- [ ] 单测 ≥6

**依赖**：Q1 · **预估**：3～5d

---

## Q6 — 规划质量 eval + tag

**标题**：`[Phase Q] Q6 Plan quality gate — baseline + CI + tag`

**目标**：规划质量回归门禁；文档与 demo 同步；打 tag。

**验收**：
- [ ] `eval/plan_baseline.jsonl` + `eval/plan_quality_gate.py`
- [ ] CI 接入（mock LLM）
- [ ] `demo-walkthrough.md` 增 Phase Q 段
- [ ] Tag `phase-q-advanced-planning`

**依赖**：Q1～Q5（至少 Q1+Q2+Q3） · **预估**：2～3d
