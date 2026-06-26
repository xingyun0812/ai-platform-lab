# Phase Q Q4 — Plan-level HITL

> **Issue**: #119 · [Phase Q] Q4 Plan-level HITL — approve plan before execute  
> **Status**: Implemented  
> **Branch**: `feat/issue-119-plan-level-hitl`

---

## 背景

Phase E 已实现**工具级 HITL**（`packages/agent/hitl.py`），允许在单个工具调用前暂停等待人工审批。

Q4 目标：在更高层级引入 **Plan 级审批**——在执行任何 step 前，先暂停让用户/审批员审批整个 Plan，通过后才开始执行。

---

## 设计要点

### 审批流程

```
generate_plan()
    ↓
execute_plan_with_agent(require_plan_approval=True)
    ↓
  [store_plan_approval(uuid, plan, tenant_id)]
    ↓
  return { status: "pending_plan_approval", plan_approval_id: "...", plan: {...} }
    ↓
  [用户调用 POST /v1/agent/plan/approval/{id}/approve]
    ↓
  approve_plan(id) → status = "approved"
    ↓
  [重新触发 execute_plan_with_agent, require_plan_approval=False]
    ↓
  正常执行所有 steps
```

### 拒绝流程

```
[用户调用 POST /v1/agent/plan/approval/{id}/reject]
    ↓
  reject_plan(id) → status = "rejected"
    ↓
  [调用方收到 rejected，不再执行]
```

---

## 数据模型

### Plan Approval Entry（内存 dict）

| 字段 | 类型 | 说明 |
|------|------|------|
| `plan` | `AgentPlan` | 待审批的 Plan 对象 |
| `tenant_id` | `str` | 发起审批的租户 |
| `status` | `str` | `pending` / `approved` / `rejected` |
| `created_at` | `str` | ISO 8601 UTC 时间戳 |

---

## REST API

| 方法 | 路径 | 描述 | 权限 |
|------|------|------|------|
| `GET` | `/v1/agent/plan/approval/{plan_approval_id}` | 查询 plan 审批状态 + plan JSON | `platform_admin` |
| `POST` | `/v1/agent/plan/approval/{plan_approval_id}/approve` | 审批通过 | `platform_admin` |
| `POST` | `/v1/agent/plan/approval/{plan_approval_id}/reject` | 拒绝 plan | `platform_admin` |

### GET 响应示例

```json
{
  "plan_approval_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "my-tenant",
  "status": "pending",
  "created_at": "2026-06-25T00:00:00Z",
  "plan": {
    "goal": "分析用户行为",
    "steps": [
      {"id": "s1", "description": "获取数据", "tool_hint": "get_kb_snippet", "depends_on": []}
    ]
  }
}
```

### POST /approve 响应示例

```json
{
  "plan_approval_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "approved",
  "approved_by": "admin-tenant"
}
```

---

## 配置表

| 字段名 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| *(无新 settings 字段)* | — | — | plan 审批纯内存，无需额外配置 |

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `packages/agent/plan_approval.py` | Plan 审批 store（5 个公共函数 + reset） |
| `apps/gateway/agent/plan_approval_routes.py` | 3 个 REST 路由 |
| `tests/test_plan_approval.py` | ≥ 12 个单测 |
| `docs/phase-q-plan-hitl.md` | 本文档 |

## 修改文件

| 文件 | 变更 |
|------|------|
| `packages/agent/planner.py` | 新增 `format_plan_summary()` + `execute_plan_with_agent(require_plan_approval=)` + `execute_plan_parallel(require_plan_approval=)` |

---

## 测试覆盖

```
tests/test_plan_approval.py
  TestStorePlanApproval
    test_store_plan_approval_creates_entry         ✓
    test_get_plan_approval_nonexistent             ✓
    test_store_multiple_entries_are_isolated       ✓
    test_reset_clears_all_entries                  ✓
  TestApprovePlan
    test_approve_plan_success                      ✓
    test_approve_plan_nonexistent                  ✓
    test_reject_plan_success                       ✓
    test_reject_plan_nonexistent                   ✓
    test_is_plan_approved_pending                  ✓
    test_is_plan_approved_nonexistent              ✓
  TestFormatPlanSummary
    test_format_plan_summary_contains_goal         ✓
    test_format_plan_summary_contains_steps        ✓
    test_format_plan_summary_contains_step_ids     ✓
    test_format_plan_summary_includes_tool_hint    ✓
  TestExecutePlanWithAgentPlanApproval
    test_execute_plan_with_agent_pending_approval_when_required  ✓
    test_execute_plan_with_agent_no_approval_default             ✓
    test_execute_plan_parallel_pending_plan_approval             ✓
  TestPlanApprovalRoutes
    test_plan_approval_routes_get_not_found        ✓
    test_plan_approval_routes_get_success          ✓
    test_plan_approval_routes_approve              ✓
    test_plan_approval_routes_reject               ✓
    test_plan_approval_routes_approve_not_found    ✓
```

---

## 代码导航

- `packages/agent/plan_approval.py` — Plan 审批内存 store
- `packages/agent/planner.py` — `format_plan_summary()`, `execute_plan_with_agent(..., require_plan_approval)`, `execute_plan_parallel(..., require_plan_approval)`
- `apps/gateway/agent/plan_approval_routes.py` — REST 路由
- `tests/test_plan_approval.py` — 单测

---

## 集成指南（父 Agent 操作）

### `apps/gateway/main.py` 中添加

```python
from apps.gateway.agent.plan_approval_routes import router as plan_approval_router
app.include_router(plan_approval_router)
```

### `apps/gateway/settings.py` 中无新字段

Plan 审批使用纯内存 store，无需新增 settings 字段。

### `.env.example` 无新变量

### `README.md` 新增章节

```markdown
## Plan-level HITL (Q4)

Set `require_plan_approval=True` when calling `execute_plan_with_agent` / `execute_plan_parallel`
to pause before executing any step and require human approval of the full plan.

API endpoints:
- `GET /v1/agent/plan/approval/{id}` — query approval status
- `POST /v1/agent/plan/approval/{id}/approve` — approve plan
- `POST /v1/agent/plan/approval/{id}/reject` — reject plan
```

---

## 已知限制

1. **内存存储**：重启后审批记录丢失；生产环境应替换为 Redis/DB 实现
2. **无超时机制**：pending 记录不会自动过期（Phase E tool-level HITL 有 TTL，plan-level 未实现）
3. **无 webhook 通知**：审批等待时不会主动通知审批员（对比 `packages/hitl/webhook.py`）
4. **replan 后不重新暂停**：`_replan_attempt > 0` 时跳过 plan 审批，避免无限循环
5. **执行延续需手动触发**：approve 后调用方需自行重新调用 `execute_plan_*`（无内置续跑机制）

---

## 面试要点

1. **为什么在执行前做 Plan 级审批？** 工具级 HITL 粒度太细（每个工具调用都暂停），Plan 级可以在用户确认整体策略后批量执行，减少打扰同时保留关键控制点。
2. **状态机设计**：`pending → approved → [执行]` / `pending → rejected → [终止]`，状态转换不可逆（简化竞争条件处理）。
3. **线程安全**：使用 `threading.RLock` 保护全局 dict，兼容 FastAPI 异步环境中的同步写操作。
4. **向后兼容**：`require_plan_approval` 默认为 `False`，不影响现有调用路径。
5. **可测试性**：`reset_plan_approval_store_for_tests()` 允许每个测试独立重置状态，避免测试间污染。
