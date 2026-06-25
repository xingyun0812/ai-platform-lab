# Phase R — R2 Long-horizon Task: Checkpoint + Resume Across Sessions

> **Issue**: [#135](https://github.com/xingyun0812/ai-platform-lab/issues/135)  
> **Milestone**: Phase R — Agent Harness Frontier (#7)  
> **Status**: Implemented

---

## 1. Background & Motivation

Phase Q introduced Plan + Replan with a hard ceiling (`max_replan_attempts=2`). Tasks longer than a single session could not be paused and resumed — if the process died or a user closed the session, all progress was lost.

**R2** adds:
- **Persistent long-run tasks** (in-memory; Postgres/Redis left for follow-up)
- **Per-layer auto-checkpointing** every time a DAG execution layer completes
- **Resume from checkpoint** — reload the latest snapshot and skip already-completed steps
- **Admin-visible full task list** with progress percentage

---

## 2. Design Points

| Concern | Decision |
|---|---|
| Storage backend | In-memory (`LongRunTaskStore`) — Postgres/Redis as post-MVP |
| Thread safety | `threading.RLock` throughout |
| Checkpoint trigger | After each DAG layer completes inside `execute_plan_parallel` |
| Resume semantics | Load latest checkpoint → restore `step_states` → set status `running` |
| Skip logic | `completed_step_ids` set built from latest snapshot; pending_steps = layer − completed |
| Singleton pattern | `init` / `get` / `reset_for_tests` — same as every other package |
| Auto-checkpoint on failure | Only triggered when `layer_completed=True` and `last_status != failed` |

---

## 3. Data Model

```
LongRunTask
  ├── task_id: str (UUID)
  ├── tenant_id: str
  ├── session_id: str
  ├── plan: AgentPlan
  ├── step_states: list[StepState]
  ├── status: pending|running|paused|completed|failed|cancelled
  ├── created_at: float
  ├── updated_at: float
  ├── checkpoints: list[Checkpoint]
  ├── final_result: dict | None
  └── metadata: dict

StepState
  ├── step_id: str
  ├── status: pending|running|completed|failed|skipped
  ├── started_at: float | None
  ├── completed_at: float | None
  ├── sub_session_id: str | None
  ├── tool_calls_summary: list[dict]
  └── error: str | None

Checkpoint
  ├── checkpoint_id: str (UUID)
  ├── task_id: str
  ├── step_states: list[StepState]   ← full snapshot copy
  ├── layer_index: int                ← # completed steps at snapshot time
  └── created_at: float
```

---

## 4. REST API

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/agent/long-run` | Create long-run task |
| `GET` | `/v1/agent/long-run` | List all tasks for tenant |
| `GET` | `/v1/agent/long-run/{task_id}` | Get task status + progress |
| `POST` | `/v1/agent/long-run/{task_id}/resume` | Resume from latest checkpoint |
| `POST` | `/v1/agent/long-run/{task_id}/cancel` | Cancel task |

### Auth
All routes require `X-Tenant-Id` + `Authorization: Bearer <token>`.

### POST /v1/agent/long-run
```json
{
  "plan": {
    "goal": "Generate monthly report",
    "steps": [
      {"id": "s1", "description": "Fetch data", "depends_on": []},
      {"id": "s2", "description": "Analyze data", "depends_on": ["s1"]},
      {"id": "s3", "description": "Write report", "depends_on": ["s2"]}
    ]
  },
  "session_id": "sess-abc123",
  "metadata": {"priority": "high"}
}
```

Response `201`:
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "tenant_id": "tenant1",
  "progress": {"total": 3, "completed": 0, "failed": 0, "pending": 3, "percent": 0.0}
}
```

---

## 5. Code Navigation

| File | Purpose |
|---|---|
| `packages/agent/long_horizon.py` | Core data model + store + convenience functions |
| `apps/gateway/agent/long_run_routes.py` | FastAPI REST API (5 routes) |
| `packages/agent/planner.py` | `execute_plan_parallel` extended with `long_run_task_id` + auto-checkpoint |
| `tests/test_long_horizon.py` | 41 unit tests |
| `eval/long_horizon_smoke.py` | Cross-session smoke test (3 sessions) |

---

## 6. Configuration Table

| Field Name | Env Var | Default | Description |
|---|---|---|---|
| `long_run_task_enabled` | `LONG_RUN_TASK_ENABLED` | `True` | Feature flag (future use) |

> No new settings fields are required for the current in-memory implementation. The gateway router just needs to be wired.

---

## 7. Integration Instructions for Shared Files

### `apps/gateway/main.py` — add after existing agent router imports

```python
from apps.gateway.agent.long_run_routes import router as long_run_router
app.include_router(long_run_router)
```

### `.env.example` — (no new env vars required for in-memory backend)

```
# Phase R R2 — Long-horizon tasks (optional, future Redis backend)
# LONG_RUN_REDIS_URL=redis://localhost:6379/2
```

### `README.md` — add to Feature Matrix

```markdown
| R2 | Long-horizon task | Checkpoint + resume across sessions | `POST /v1/agent/long-run` |
```

### `docs/roadmap.md` — add Phase R entry

```markdown
### R2 — Long-horizon Task (closes #135)
- In-memory checkpoint store with per-layer auto-checkpointing
- Resume from latest checkpoint; skip already-completed steps
- REST: 5 routes under `/v1/agent/long-run`
```

---

## 8. Test Coverage

```
TestStepState            — 3 cases (defaults, to_dict, status transitions)
TestCheckpoint           — 2 cases (to_dict, serialization roundtrip)
TestLongRunTask          — 5 cases (to_dict, progress all variants)
TestLongRunTaskStore     — 11 cases (CRUD + checkpoint + cancel/delete)
TestResumeTask           — 4 cases (from checkpoint, fresh, nonexistent, snapshot capture)
TestGetTaskStatus        — 2 cases (combined result, missing)
TestExecutePlanParallelLongRun — 3 cases (skip completed, auto-checkpoint, backward compat)
TestLongRunRoutes        — 8 cases (POST/GET/list/resume/cancel/409/401)
TestUtilFunctions        — 3 cases (UUID uniqueness, cancel convenience)

Total: 41 tests
```

---

## 9. Resume Flow Example

```python
from packages.agent.long_horizon import (
    create_long_run,
    checkpoint_task,
    resume_task,
    get_long_run_store,
    get_task_status,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep

# -- Session 1: Create and run first step --
plan = AgentPlan(goal="Multi-day analysis", steps=[
    PlanStep(id="s1", description="Fetch data", depends_on=[]),
    PlanStep(id="s2", description="Analyze", depends_on=["s1"]),
    PlanStep(id="s3", description="Report", depends_on=["s2"]),
])
task = create_long_run(plan, tenant_id="acme", session_id="day1")
store = get_long_run_store()

# ... run s1 ...
task.step_states[0].status = "completed"
store.update_step_states(task.task_id, task.step_states)

cp = checkpoint_task(task.task_id)   # auto-saved: layer_index=1
store.update_status(task.task_id, "paused")

# -- Session 2 (next day): Resume --
resumed = resume_task(task.task_id)
# resumed.step_states[0].status == "completed"  ← restored from checkpoint
# resumed.status == "running"

# Pass to execute_plan_parallel for skipping completed steps:
await execute_plan_parallel(
    plan=plan,
    long_run_task_id=task.task_id,  # ← R2 key
    ...
)
```

---

## 10. Known Limits

1. **In-memory only**: process restart loses all tasks. Postgres/Redis backend is the next milestone.
2. **No distributed locking**: single-node; multi-replica setups need an external lock.
3. **Checkpoint granularity**: per-layer (not per-step). Fine-grained sub-step checkpoints are future work.
4. **No TTL/eviction**: the in-memory store grows unbounded. A TTL eviction policy should be added.
5. **No streaming status**: clients must poll `GET /v1/agent/long-run/{task_id}`. SSE/WebSocket is follow-up.

---

## 11. Interview Talking Points

- **Why in-memory first?** Enables fast iteration and full test coverage without infrastructure dependencies. Storage is abstracted behind `LongRunTaskStore` so swapping to Postgres is a one-file change.
- **Checkpoint vs. re-execution**: R2 checkpoints at the coarse DAG-layer level. Fine-grained step-level checkpoints are possible with the same data model — just call `checkpoint_task` more frequently.
- **Thread safety approach**: `threading.RLock` (reentrant) allows the same thread to acquire the lock twice, preventing deadlocks in nested calls.
- **Backward compatibility**: `execute_plan_parallel` gains `long_run_task_id=None` (default). All existing callers work unchanged.
- **Resume semantics**: We copy step_states into the checkpoint (deep clone), so modifying the live task doesn't corrupt historical checkpoints.
