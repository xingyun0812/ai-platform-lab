# Phase R — R1 Self-evolving Agent

> **Issue**: #134 · **Milestone**: Phase R — Agent Harness Frontier (#7)
> **Branch**: `feat/issue-134-self-evolving-agent`

---

## 1. 背景与设计目标

Agent 执行完任务后，除了反馈飞轮（已有，迭代 Prompt）外，还应把**成功路径**沉淀为「经验」；下次相似任务时优先复用。策略自改必须走 HITL，避免失控。

### 核心设计原则

| 原则 | 说明 |
|------|------|
| **经验优先** | 相同任务 signature 时，优先注入历史成功经验 |
| **HITL 守门** | 策略 patch 入库 → 等待人工审批 → 不直接修改代码 |
| **异常隔离** | 所有自进化步骤包裹 try/except，失败只 log warning |
| **无副作用** | 经验注入只影响 prompt context，不改变 plan 逻辑 |
| **内存实现** | Phase R 阶段纯内存，Postgres + Redis 留作后续 |

---

## 2. 数据模型

### ExperienceRecord

```python
@dataclass
class ExperienceRecord:
    experience_id: str          # UUID
    tenant_id: str
    task_signature: str         # SHA1(goal)[:16]
    goal: str
    plan: AgentPlan
    tool_calls: list[dict]
    outcome: str                # "success" | "partial" | "failed"
    lessons: str                # LLM 反思文本
    created_at: float           # Unix timestamp
    metadata: dict
```

### StrategyPatch

```python
@dataclass
class StrategyPatch:
    patch_id: str
    tenant_id: str
    lessons: str
    proposed_change: dict       # {field, old, new, reason}
    status: str                 # "pending" | "approved" | "rejected"
    created_at: float
    decided_at: float | None
    decided_by: str | None
```

---

## 3. 核心流程

```
Agent 执行完成
      │
      ▼
trigger_self_evolve(plan, outcome, tenant_id)
      │
      ├─ [1] store_experience()  ──→ ExperienceStore (内存)
      │
      ├─ [2] reflect_on_run()    ──→ LLM 生成 lessons
      │                                    │ 失败 → fallback template
      │
      └─ [3] maybe_patch_strategy() ──→ StrategyPatch (pending)
                                              │
                                         HITL 审批
                                         ├─ approve → status=approved (仅入库)
                                         └─ reject  → status=rejected

下次相同任务:
generate_plan(goal)
      │
      ├─ compute_task_signature(goal)
      ├─ retrieve_similar_experiences(sig)
      └─ 注入 past_lessons 到 context → 优化 plan 质量
```

---

## 4. REST API（无新增路由，通过 planner 隐式使用）

自进化功能通过 `generate_plan` 的经验注入隐式启用，无需额外 API 调用。

如需后续扩展，建议在 `apps/gateway/agent_routes.py` 添加：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/internal/agent/experiences` | 列出所有经验 |
| `POST` | `/internal/agent/experiences` | 手动存储经验 |
| `DELETE` | `/internal/agent/experiences/{id}` | 删除经验 |
| `GET` | `/internal/agent/strategy-patches` | 列出策略 patch |
| `POST` | `/internal/agent/strategy-patches/{id}/approve` | HITL 审批 |
| `POST` | `/internal/agent/strategy-patches/{id}/reject` | HITL 拒绝 |

---

## 5. 配置表

| 字段名 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `self_evolve_enabled` | `SELF_EVOLVE_ENABLED` | `true` | 是否启用自进化（后续） |
| `self_evolve_max_patches_per_day` | `SELF_EVOLVE_MAX_PATCHES_PER_DAY` | `5` | 每日最多策略 patch 数 |
| `self_evolve_experience_top_k` | `SELF_EVOLVE_EXPERIENCE_TOP_K` | `2` | 注入经验数量上限 |

> 注：当前配置通过 `StrategyPatchStore(max_patches_per_day=N)` 传入，后续可从 settings 读取。

---

## 6. Prometheus 指标

新增两个 counter 指标：

```
# HELP agent_self_evolve_experiences_total Total experiences stored
# TYPE agent_self_evolve_experiences_total counter
agent_self_evolve_experiences_total{tenant="t1"} 42

# HELP agent_self_evolve_strategy_patches_total Total strategy patches proposed
# TYPE agent_self_evolve_strategy_patches_total counter
agent_self_evolve_strategy_patches_total{tenant="t1"} 7
```

---

## 7. 测试

```bash
# 单测 (23 个用例)
python3 tests/test_self_evolve.py

# Smoke test
python3 eval/self_evolve_smoke.py
```

测试覆盖：
- `TestExperienceStore` (8 用例): store/get/retrieve_similar/retrieve_by_goal/list_all/delete/sig_index/to_dict
- `TestReflectOnRun` (3 用例): LLM 正常/LLM 失败回退/空 tool_calls
- `TestMaybePatchStrategy` (3 用例): 生成 patch/空 lessons/每日上限
- `TestApproveRejectStrategyPatch` (3 用例): approve/reject/不存在 ID
- `TestTriggerSelfEvolve` (3 用例): 经验入库/返回 lessons/异常隔离
- `TestPlannerExperienceInjection` (3 用例): 注入经验/失败降级/签名确定性

---

## 8. 代码导航

| 文件 | 说明 |
|------|------|
| `packages/agent/experience_store.py` | 经验库核心（ExperienceRecord + ExperienceStore + 全局单例） |
| `packages/agent/self_evolve.py` | 自进化主循环（reflect_on_run + maybe_patch_strategy + trigger_self_evolve） |
| `packages/agent/planner.py` | `generate_plan` 集成经验注入（Phase R R1 注释块） |
| `packages/agent/perf_metrics.py` | 新增 2 个 Prometheus 指标 |
| `tests/test_self_evolve.py` | 23 个单测 |
| `eval/self_evolve_smoke.py` | Smoke 测试（3 个场景） |

---

## 9. Shared 文件集成说明

> 由父 Agent 集成，本分支不直接修改以下文件。

### `apps/gateway/settings.py` 新增字段

```python
self_evolve_enabled: bool = Field(
    default=True,
    validation_alias="SELF_EVOLVE_ENABLED",
    description="是否启用 R1 自进化功能",
)
self_evolve_max_patches_per_day: int = Field(
    default=5,
    validation_alias="SELF_EVOLVE_MAX_PATCHES_PER_DAY",
    description="每日最多策略 patch 生成数",
)
self_evolve_experience_top_k: int = Field(
    default=2,
    validation_alias="SELF_EVOLVE_EXPERIENCE_TOP_K",
    description="generate_plan 时注入历史经验数量上限",
)
```

### `.env.example` 新增

```bash
# Phase R R1 Self-Evolving Agent
SELF_EVOLVE_ENABLED=true
SELF_EVOLVE_MAX_PATCHES_PER_DAY=5
SELF_EVOLVE_EXPERIENCE_TOP_K=2
```

### `README.md` 新增章节

```markdown
## Phase R — R1 Self-evolving Agent

Agent 执行完任务后自动沉淀「经验」，下次相似任务时注入历史 lessons 优化 Plan 质量。
策略改进建议（StrategyPatch）需经 HITL 审批，不直接修改代码。

- 经验库：`packages/agent/experience_store.py`
- 自进化主循环：`packages/agent/self_evolve.py`
- 触发方式：`trigger_self_evolve(plan, outcome, tenant_id=...)` 异步非阻塞
```

### `docs/roadmap.md` 更新

```markdown
- [x] Phase R R1: Self-evolving Agent — experience store + strategy patch (closes #134)
```

---

## 10. 已知限制

| 限制 | 说明 |
|------|------|
| 内存存储 | 重启后经验丢失，Postgres 持久化留 Phase R2 |
| 简单 similarity | 用 SHA1(goal) 精确匹配，未用 embedding 语义相似 |
| 无 embedding | `retrieve_by_goal` 降级为子串匹配，召回率有限 |
| StrategyPatch 仅入库 | approved patch 需人工或父 Agent 手动集成到代码 |
| LLM lessons 质量 | 依赖 LLM 反思能力，低质量 prompt 可能生成无用 lessons |

---

## 11. 面试谈资

1. **为什么 HITL 是必须的？** 策略自改如果直接生效，会引发"失控的自我修改"问题。HITL 提供人工兜底，是 AI Safety 的基本保障。

2. **task_signature 的设计权衡？** 用 SHA1(goal)[:16] 是最简实现，优点是确定性强、无依赖；缺点是语义相似但字面不同的任务无法命中。下一步用 embedding + 余弦相似度解决。

3. **为什么经验注入不改 Plan 结构，只改 context？** 避免引入非预期的 Plan 偏差。lessons 作为"参考建议"注入，由 LLM 自行决定是否采纳，更安全可控。

4. **StrategyPatch 的 approved 状态为什么只入库？** 避免自动修改代码造成不可逆后果。approved patch 的应用需要代码审查，由 CI/CD 流程保障质量。

5. **异常隔离的重要性？** `trigger_self_evolve` 是异步后台流程，不应影响主流程返回。任何步骤失败只 log warning，不上报给用户，保证体验一致性。
