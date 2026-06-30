# ADR-0001: Phase Q7 与 R2 三张 ID 边界

- **Status**: accepted
- **Date**: 2026-06-09
- **Issue**: Phase R #133–#137
- **Tags**: phase-q, phase-r, agent, harness

## Context

Phase Q7（Graph Runtime / Orchestrator）与 Phase R2（长程任务）都涉及 checkpoint / resume。若合并 ID 或字段名，Console、API、审计与面试叙事会打架。

## Decision

**三个 ID 永不合并，API 字段名保持区分：**

| ID | 层次 | 场景 |
|----|------|------|
| `plan_approval_id` | Q4/Q7 | Plan 生成后、执行前的人批 |
| `execution_id` | Q7 Orchestrator | YAML 工作流节点级 checkpoint |
| `task_id` | R2 `long_horizon` | 跨 session 长程 Plan 任务 |

分工：`Q7` 管「怎么跑起来」；`R2` 管「任务跑多久」。`AgentGraphState` 可 **引用** `long_run_task_id`，不在 R2 重复实现 Orchestrator 引擎。

## Consequences

### Positive

- 联调与文档可逐层讲清；HITL / 运维 / 长程列表各用各的主键

### Negative / trade-offs

- 用户需记住三种 resume 参数；Console 需分入口（长程列表仍为 follow-up）

### Follow-up

- [ ] Console 长程任务列表
- [x] 统一「状态查询」只读 API → `GET /v1/agent/execution-status`（#169 PR-2）

## Alternatives considered

| 方案 | 为何未选 |
|------|----------|
| 统一为一个 `resume_id` | 语义丢失，持久化策略与 resume 逻辑无法分治 |
| 只用 `execution_id` 覆盖 Plan step | Orchestrator 节点状态 ≠ Plan step 状态机 |

## References

- `packages/agent/graph_runtime.py`
- `packages/agent/long_horizon.py`
- `packages/agent/orchestrator/checkpoint_engine.py`
- `docs/phase-r-agent-harness.md` §2.1
