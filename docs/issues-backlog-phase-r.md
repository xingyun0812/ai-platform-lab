# Phase R Issue Backlog — Agent Harness 前沿

> 规划：[phase-r-agent-harness.md](./phase-r-agent-harness.md)（含 [Q7 边界](./phase-r-agent-harness.md#21-phase-q-q7-与-phase-r-边界避免概念打架)、[业界定位](./phase-r-agent-harness.md#7-业界定位与诚实边界非-sota-表述)）  
> **Milestone**：Phase R — Agent Harness Frontier  
> **Tag**：`phase-r-agent-harness`  
> **门禁**：`python eval/harness_capability_gate.py run`（7/7）  
> **前置**：Phase Q ✅（#121） · Phase F #31 长记忆 ✅ · Phase J #48 反馈飞轮 ✅

| Backlog | GitHub Issue | 状态 |
|---------|--------------|------|
| R0 规划文档 | [#133](https://github.com/xingyun0812/ai-platform-lab/issues/133) | ✅ |
| R1 自进化 Agent | [#134](https://github.com/xingyun0812/ai-platform-lab/issues/134) | ✅ PR #138 |
| R2 长程任务持久化 | [#135](https://github.com/xingyun0812/ai-platform-lab/issues/135) | ✅ PR #138（Console 见遗留） |
| R3 模型能力探测 | [#136](https://github.com/xingyun0812/ai-platform-lab/issues/136) | ✅ PR #141 |
| R4 eval 门禁 + tag | [#137](https://github.com/xingyun0812/ai-platform-lab/issues/137) | ✅ |

---

## R0 — 规划总览

**标题**：`[Phase R] Agent Harness Frontier — 自进化 / 长程任务 / 模型能力探测规划`

**目标**：评审 `docs/phase-r-agent-harness.md`，创建 R1～R4 Issue，更新 `roadmap.md`。

**验收**：
- [x] `docs/phase-r-agent-harness.md` 评审通过（含 Q7 边界 + 非 SOTA 表述 §7）
- [x] Milestone Phase R 内 R1～R4 Issue 创建完毕（#134～#137）
- [x] `roadmap.md` 增加 Phase R 章节

**非目标**：训练模型；在线 RL；亿级在线推理。

---

## R1 — 自进化 Agent（经验库 + 策略自改）

**标题**：`[Phase R] R1 Self-evolving Agent — experience store + strategy patch`

**目标**：Agent 跑完任务后自动沉淀经验；下次相似任务复用；策略修改走 HITL。

**验收**：
- [x] `packages/agent/experience_store.py` — 经验库（Postgres + 内存降级；embedding 相似检索）
  - `ExperienceRecord`: task_signature + plan + tool_calls + outcome + lessons
  - `store_experience()` / `retrieve_similar_experiences(task, top_k=3)`
- [x] `packages/agent/self_evolve.py` — 自进化主循环
  - `reflect_on_run(plan, outcome)` → LLM 生成 lessons
  - `maybe_patch_strategy(lessons, current_strategy)` → 修改 plan_prompt / tool_selection
  - 策略变更走 HITL（不能静默改）
- [x] Planner 集成：`generate_plan` 先查经验库 → 注入 plan_prompt
- [x] Prometheus：`agent_self_evolve_experiences_total` / `agent_self_evolve_strategy_patches_total`
- [x] 单测 ≥ 12（`tests/test_self_evolve.py` 23 + `tests/test_experience_persistence.py` 23）
- [x] `eval/self_evolve_smoke.py` — 同类任务第 2 次复用经验

**浅集成 gap（模块 ✅，主路径未闭合）** — 详见 [architecture-deepening-todo.md §7](./architecture-deepening-todo.md#7-phase-r-深存储--浅集成)：

| 子项 | 说明 | 状态 |
|------|------|------|
| 7a 写路径 | `trigger_self_evolve` 挂 `graph_runtime` 终态（`run_lifecycle` + `create_task`） | 🔄 [#146](https://github.com/xingyun0812/ai-platform-lab/issues/146) |
| 7b REST | strategy patch HTTP；`apps/gateway/agent/strategy_patch_routes.py` | 🔄 feat/issue-146 |
| 7c Planner | approve 注入 `generate_plan` | 🔄 PR #148 |
| E2E | REST approve → plan（`test_self_evolve_e2e.py`） | 🔄 PR #148 |
| 7d 持久化 | `StrategyPatchStore` 纯内存；experience 已有 Postgres | ⬜ |

**依赖**：Phase Q ✅ · Phase F #31 长记忆 ✅ · **预估**：5～7d（模块）+ 3～5d（#7 浅集成闭合）

---

## R2 — 跨 session 长程任务（checkpoint + resume）

**标题**：`[Phase R] R2 Long-horizon task — checkpoint + resume across sessions`

**目标**：任务可跨天/跨 session 运行；随时挂起，随时续跑；管理员可见全貌。

**验收**：
- [x] `packages/agent/long_horizon.py` — 长程任务管理
  - `LongRunTask`: task_id + plan + step_states[] + checkpoints[] + status
  - `create_long_run(plan, ...)` → 入库返回 task_id
  - `checkpoint_task(task_id)` → 持久化当前状态 + 中间产物
  - `resume_task(task_id)` → 加载 checkpoint，从下一未完成 step 继续
  - `cancel_task(task_id)` / `get_task_status(task_id)`
- [x] Postgres `long_run_tasks` 表 + Redis 进度缓存
- [x] `execute_plan_parallel` 增 `long_run_task_id` 参数；每完成一层 → auto-checkpoint
- [x] REST 路由：
  - `POST /v1/agent/long-run` — 创建
  - `GET /v1/agent/long-run/{task_id}` — 查询状态
  - `POST /v1/agent/long-run/{task_id}/resume` — 续跑
  - `POST /v1/agent/long-run/{task_id}/cancel` — 取消
- [x] 单测 ≥ 10（`tests/test_long_horizon.py` 41 + `tests/test_long_horizon_persistence.py` 40）
- [x] `eval/long_horizon_smoke.py` — 模拟跨 session 任务（断点续跑 2 次）
- [ ] Console 展示长程任务列表（最小）— **未做**，可用 `GET /v1/agent/long-run` + curl 演示

**依赖**：Phase Q ✅ · **预估**：4～5d

---

## R3 — Harness-side 模型能力探测

**标题**：`[Phase R] R3 Model capability profiling — 4-dim benchmark + router feedback`

**目标**：自动测出模型在 context/memory/tool/planning 4 维度的强弱；反哺 Router。

**验收**：
- [x] `eval/harness_capability_benchmark.py` — 4 维度 benchmark
  - `context_mgmt`: 长上下文召回（needle-in-haystack 变体）
  - `long_memory`: 跨 session 记忆检索准确率
  - `tool_use`: 工具调用成功率 + 参数 schema 准确率
  - `planning`: Plan 结构合理性 + 步骤数 / 依赖正确率
- [x] `packages/agent/capability_profile.py` — 能力画像
  - `ModelCapabilityProfile`: model_id + 4 维度分数 + timestamp
  - `run_capability_profile(model_id)` → 跑全部 benchmark → 入库
  - `get_profile(model_id)` / `compare_profiles(m1, m2)`
- [x] Model Router 集成：`select_model_with_capability` + `required_capability`（带工具 plan 等按维度选模型）
  - 注：静态 YAML `fallback_chains` 未改为全量「按维度匹配度排序」（follow-up）
- [x] `POST /internal/harness/capability-report` → 生成 Markdown 报告
- [x] 单测 ≥ 10（`tests/test_capability_profile.py` 37）
- [x] `eval/harness_capability_benchmark.py run --model X --mock` 可独立运行
- [x] 1 份样例报告 `docs/phase-r-capability-report-sample.md`

**依赖**：Phase Q ✅ · Phase F #31 ✅ · **预估**：4～5d

---

## R4 — eval 门禁 + tag

**标题**：`[Phase R] R4 Harness eval gate + tag`

**目标**：联合门禁 + 文档同步 + 打 tag。

**验收**：
- [x] `eval/harness_capability_gate.py` — `list` / `check` / `run` 子命令
- [x] `eval/harness_baseline.jsonl` — ≥5 case（6 条）
- [x] `demo-walkthrough.md` 增 Phase R 段
- [x] Tag `phase-r-agent-harness`（gate 全绿后指向最新 merge）

**依赖**：R1～R3 · **预估**：2～3d

---

## 遗留 / Follow-up（不阻塞 milestone）

| 项 | 说明 |
|----|------|
| **R1 写路径接线 (7a)** | `graph_runtime` 终态经 `finalize_agent_run_result` → `create_task(trigger_self_evolve)`；`SELF_EVOLVE_ENABLED=false` 可关 | 🔄 代码已合入，E2E 待验 |
| **R1 Strategy patch REST (7b)** | `GET/POST /internal/agent/strategy-patches/*` 已实现；PR 待 merge | 🔄 |
| **R1 approved → Planner (7c)** | approve 仅更新 `StrategyPatchStore` 内存 status；`generate_plan` 不读 approved patch |
| **R1 Patch 持久化 (7d)** | `StrategyPatchStore` 进程内 dict；与 `experience_store` Postgres 不对称 |
| R1 Redis 热缓存 | 经验库当前 Postgres + 内存；原规划「Redis 热缓存」未实现 |
| R2 Console | `console-v2` 无长程任务列表页 |
| R3 Router fallback | 能力感知选主模型已做；降级链仍为静态 YAML |

> **架构加深**：上述 R1 四条与 [architecture-deepening-todo.md #7](./architecture-deepening-todo.md#7-phase-r-深存储--浅集成) 对齐，优先级 P1 · RFC → [#146](https://github.com/xingyun0812/ai-platform-lab/issues/146)
