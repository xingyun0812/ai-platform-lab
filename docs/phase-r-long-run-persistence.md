# Phase R R2+ — Long-Run Task Persistence (Postgres + Redis)

## Overview

R2 (#135) implemented a purely in-memory long-run task store. This issue (R2+, #140) upgrades the store to a **three-tier persistence architecture**:

1. **Postgres backend** — full task + checkpoint persistence across process restarts
2. **Redis cache** — fast status/step_states reads without hitting Postgres
3. **In-memory fallback** — zero-dependency development/test mode

## Design Points

### Class Hierarchy

```
LongRunTaskStore (abstract base)
├── InMemoryLongRunTaskStore   — thread-safe dict + defaultdict
├── PostgresLongRunTaskStore   — psycopg v3 + dict_row + JSONB
└── RedisLongRunCache(fallback_store)  — decorator pattern
```

### Auto-Selection Strategy (`get_long_run_store()`)

```
DATABASE_URL set?
├── Yes: try PostgresLongRunTaskStore
│   └── connection fails? → InMemoryLongRunTaskStore (warn)
└── No:  InMemoryLongRunTaskStore

REDIS_URL set (after base selected)?
├── Yes: try redis.from_url + ping
│   ├── OK     → RedisLongRunCache(base)
│   └── fails  → base (warn)
└── No:  base (no decoration)
```

### Redis Cache Strategy

- **Key**: `ai_platform:long_run:{task_id}` (Redis hash)
- **Fields**: `status`, `step_states_json`, `updated_at`
- **TTL**: 3600 seconds
- **On write** (`update_status`, `update_step_states`, `add_checkpoint`, `cancel`, `delete`): write fallback → `DEL` key
- **On read** (`get`): `HGETALL` → hit: overlay status/step_states on full task from store; miss: query store → `HSET`

### Cross-Process Resume Flow

```
Process A                                Process B (new instance)
─────────────────────────────────────    ────────────────────────────────────
create_long_run(plan, tenant)            
mark step_states[0].status = completed  
update_step_states(task_id, ...)        
add_checkpoint(task_id, checkpoint)     
(process exits / crashes)               
                                         store = get_long_run_store()  # new instance
                                         task = await store.get(task_id)
                                         cp = await store.get_latest_checkpoint(task_id)
                                         await store.update_step_states(task_id, cp.step_states)
                                         await store.update_status(task_id, "running")
                                         # Execute only uncompleted steps
                                         completed_ids = {s.step_id for s in cp.step_states
                                                          if s.status == "completed"}
```

## Data Model

### Postgres Schema

```sql
CREATE TABLE IF NOT EXISTS long_run_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    plan_json JSONB NOT NULL,
    step_states_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    final_result_json JSONB
);
CREATE INDEX IF NOT EXISTS idx_long_run_tenant ON long_run_tasks(tenant_id);

CREATE TABLE IF NOT EXISTS long_run_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_states_json JSONB NOT NULL,
    layer_index INTEGER NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    FOREIGN KEY (task_id) REFERENCES long_run_tasks(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON long_run_checkpoints(task_id, created_at DESC);
```

### Python Dataclasses

| Class | Key Fields |
|---|---|
| `StepState` | `step_id`, `status`, `started_at`, `completed_at`, `error` |
| `Checkpoint` | `checkpoint_id`, `task_id`, `step_states`, `layer_index`, `created_at` |
| `LongRunTask` | `task_id`, `tenant_id`, `plan`, `step_states`, `status`, `checkpoints`, `final_result` |

## REST API

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/agent/long-run` | Create long-run task |
| `GET` | `/v1/agent/long-run` | List tenant's tasks |
| `GET` | `/v1/agent/long-run/{task_id}` | Get task status + progress |
| `POST` | `/v1/agent/long-run/{task_id}/resume` | Resume from latest checkpoint |
| `POST` | `/v1/agent/long-run/{task_id}/cancel` | Cancel task |

## Configuration Table

> **Note**: Add these to `apps/gateway/settings.py` and `.env.example`.

| Field | Env Var | Default | Description |
|---|---|---|---|
| `database_url` | `DATABASE_URL` | `""` | Postgres connection string. If empty, falls back to in-memory store |
| `redis_url` | `REDIS_URL` | `""` | Redis connection URL. If empty or unreachable, Redis cache is skipped |

## Shared-File Integration Instructions

### `apps/gateway/settings.py`
These fields are already present via `DATABASE_URL` / `REDIS_URL` env vars read directly in `packages/agent/long_horizon.py`. No new settings fields are required.

### `apps/gateway/main.py`
The long-run router is already included in the existing setup via `apps/gateway/agent/long_run_routes.py`. No changes needed.

### `.env.example`
Add the following:
```bash
# Long-run task persistence (R2+)
DATABASE_URL=postgresql://user:password@localhost:5432/ai_platform
REDIS_URL=redis://localhost:6379/0
```

### `README.md`
Add a section under "Backend Persistence":
```markdown
## Long-Run Task Persistence

Long-run tasks automatically use Postgres + Redis when configured:

- `DATABASE_URL` — Postgres for cross-process task persistence
- `REDIS_URL` — Redis for fast status/progress caching

Without these env vars, tasks are stored in-memory (lost on restart).
```

## Code Navigation

| Symbol | File | Description |
|---|---|---|
| `LongRunTaskStore` | `packages/agent/long_horizon.py` | Abstract base class |
| `InMemoryLongRunTaskStore` | `packages/agent/long_horizon.py` | Thread-safe dict store |
| `PostgresLongRunTaskStore` | `packages/agent/long_horizon.py` | psycopg v3 Postgres store |
| `RedisLongRunCache` | `packages/agent/long_horizon.py` | Redis decorator cache |
| `get_long_run_store()` | `packages/agent/long_horizon.py` | Auto-select backend |
| `reset_long_run_store_for_tests()` | `packages/agent/long_horizon.py` | Test helper |
| Long-run routes | `apps/gateway/agent/long_run_routes.py` | FastAPI router |
| Planner integration | `packages/agent/planner.py` | `execute_plan_parallel` async calls |
| Tests | `tests/test_long_horizon.py` | 41 tests (full coverage) |
| New persistence tests | `tests/test_long_horizon_persistence.py` | 40 tests (persistence-specific) |

## Test Summary

| Test Class | Cases | Coverage |
|---|---|---|
| `TestInMemoryLongRunTaskStore` | 12 | create/get/list/update_status/checkpoint/cancel/delete |
| `TestPostgresLongRunTaskStore` | 9 | mock psycopg, SQL verification, schema creation |
| `TestRedisLongRunCache` | 8 | cache hit/miss/backfill/invalidate/error-resilience |
| `TestBackendSelection` | 5 | Postgres/Redis/fallback auto-selection |
| `TestCrossProcessResume` | 3 | cross-process create→checkpoint→resume |
| `TestStepStateSerializationRoundtrip` | 3 | from_dict/to_dict roundtrip |

**Total new tests: 40. All pass without Postgres/Redis.**

## Known Limits

1. **psycopg v3 only** — `PostgresLongRunTaskStore` uses `psycopg` (v3) not `psycopg2`. Must have `psycopg[binary]` in requirements.
2. **No async DB driver** — PostgresLongRunTaskStore uses synchronous psycopg (blocking). For high-throughput, consider `psycopg.AsyncConnection`.
3. **Redis cache is eventually consistent** — between `update_status` write and `DEL`, a concurrent `GET` may read stale data.
4. **`checkpoints` field empty from Postgres** — `_row_to_task` returns empty checkpoints list; use `get_latest_checkpoint()` separately.
5. **No TTL for task rows** — Postgres tasks accumulate indefinitely; add a cleanup job for old completed/cancelled tasks.
6. **Thread-safety of Postgres connection** — `PostgresLongRunTaskStore` uses a single connection with `threading.RLock`. For multi-threaded servers, use a connection pool (`psycopg.pool`).

## Interview Talking Points

1. **Why Decorator Pattern for Redis?** — `RedisLongRunCache` wraps any `LongRunTaskStore` without modifying it. Follows Open/Closed principle and allows testing cache logic independently.

2. **Why JSONB for plan/step_states?** — Plans and step states are evolving JSON structures. JSONB allows schema-less evolution + Postgres JSON operators for future queries.

3. **Why `ORDER BY created_at DESC LIMIT 1` for latest checkpoint?** — Atomic and correct even under concurrent checkpoint creation. Avoids loading all checkpoints.

4. **Cross-process resume correctness** — The key insight: `execute_plan_parallel` loads completed step IDs from the store at startup, then skips them. The checkpoint stores the full `step_states` snapshot, not a delta.

5. **Graceful degradation** — Three fallback layers: Postgres → memory, Redis → no cache. Service never crashes due to missing infrastructure.

6. **All functions async** — Matches the FastAPI async pattern. Even `InMemoryLongRunTaskStore` methods are `async def` (no I/O, but consistent interface).

## Roadmap Update

> Add to `docs/roadmap.md` under Phase R:
> - [x] R2+: Long-run task persistence — Postgres + Redis + cross-process resume (closes #140)
