# ADR-0002: 三套 Checkpoint/Resume 层级与 resume 接线

- **Status**: accepted
- **Date**: 2026-06-30
- **Issue**: [#169](https://github.com/xingyun0812/ai-platform-lab/issues/169)
- **Tags**: phase-r, agent, checkpoint, architecture-deepening
- **Supersedes**: 无（补充 [ADR-0001](./0001-three-id-boundaries.md)）

## Context

Phase Q7 / R2 存在三种「暂停-继续」语义（见 ADR-0001）。#162 统一 Plan 执行轨后，R2 long_run 仍有两处缺口：

1. `execute_plan_parallel` 只 auto-checkpoint，未写 `step_states`
2. `POST /v1/agent/long-run/{id}/resume` 只恢复 store 状态，不触发 Plan 续跑

## Decision

### 三层 checkpoint（不合并 ID）

| 层 | ID | 粒度 | 持久化 | Resume 入口 |
|----|-----|------|--------|-------------|
| Plan 审批 | `plan_approval_id` | 整 Plan 执行前 | 内存 plan_approval store | `POST /v1/agent/run` + `plan_approval_id` |
| Orchestrator 节点 | `execution_id` | Workflow 节点 | graph_checkpoint store | Orchestrator checkpoint API |
| 长程 Plan 任务 | `task_id` | Plan step + 层 checkpoint | long_run_tasks / checkpoints | `POST /v1/agent/long-run/{task_id}/resume` |

### long_run resume 必须续跑 Plan

`POST .../resume` 流程：

```
resume_task(task_id)           # 从 checkpoint 恢复 step_states
  → execute_plan(..., long_run_task_id=task_id, mode=parallel)
  → record_layer_step_outcomes  # 层末写 step_states
  → checkpoint_task             # 层末快照
  → finalize_long_run_task_status
```

`long_run_task_id` 强制 **planner parallel** 轨（orchestrator 轨不支持 step_states / 层 checkpoint）。

## Consequences

### Positive

- create → 部分执行 → checkpoint → resume 可 E2E 完成 Plan
- ADR-0001 三 ID 边界保留，resume 语义各归其位

### Negative / trade-offs

- long_run resume 与 orchestrator `execution_id` resume 仍为两套 API
- Console 长程列表仍为 follow-up

## References

- `packages/agent/long_horizon.py` — `record_layer_step_outcomes`, `execute_long_run_resume`
- `packages/agent/planner.py` — `execute_plan_parallel` 层末同步
- `apps/gateway/agent/long_run_routes.py` — resume 路由
- `docs/architecture-deepening-todo.md` §5
