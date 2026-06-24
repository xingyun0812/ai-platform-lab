# Phase Q2 — DAG Parallel Plan Step Execution

> **Issue**: #117 · **Branch**: `feat/issue-117-dag-parallel-execution`  
> **Area**: `packages/agent/planner.py` + `packages/agent/perf_metrics.py`

---

## 1. Design Points

### Problem
`execute_plan_with_agent()` in `packages/agent/planner.py` executes all Plan steps **serially** in topological order. Steps with no mutual dependencies block on each other unnecessarily, increasing end-to-end latency for fan-out plans.

### Solution
Q2 introduces **layer-wise parallel execution**:

1. Group steps into **DAG layers** using BFS-based topological sort (`plan_execution_layers`).
2. Within each layer, dispatch all steps concurrently via `asyncio.gather`.
3. Each step gets its own **sub-session ID** (`{session_id}__step_{step_id}`) to avoid blackboard write conflicts.
4. Apply **fail-open** strategy: a step that raises an exception is recorded as `failed` but does not block other steps in the same layer or subsequent layers.
5. **`pending_approval`** from any step immediately stops processing further layers (preserves HITL gating semantics).

---

## 2. Data Model

No new dataclasses. Reuses existing:

| Type | Location | Notes |
|---|---|---|
| `PlanStep` | `packages/contracts/agent_schemas.py` | `id`, `description`, `depends_on: list[str]` |
| `AgentPlan` | `packages/contracts/agent_schemas.py` | `goal`, `steps: list[PlanStep]` |
| `ToolCallRecord` | `packages/contracts/agent_schemas.py` | Aggregated from all sub-sessions |

---

## 3. New APIs

### `plan_execution_layers(steps: list[PlanStep]) -> list[list[PlanStep]]`

```
Location: packages/agent/planner.py
```

Groups steps into layers using BFS topological sort (Kahn's algorithm):

1. Build `indegree` map: for each step, count how many steps it depends on.
2. Build adjacency graph: `graph[dep_id] → [step_ids that depend on dep]`.
3. Seed first layer with all steps whose `indegree == 0`.
4. After processing a layer, decrement `indegree` for all successors; any successor that reaches `indegree == 0` joins the next layer.
5. Repeat until no steps remain.

**Complexity**: O(V + E) where V = step count, E = dependency count.

**Example**:
```
s1 → s2 → s4
s1 → s3 → s4
→ [[s1], [s2, s3], [s4]]
```

---

### `execute_plan_parallel(...) -> dict[str, Any]`

```
Location: packages/agent/planner.py
```

Async function. Signature matches `execute_plan_with_agent` exactly (drop-in upgrade).

**Concurrency strategy**:
- Each step in a layer runs as `asyncio.gather(*[run_one_step(s) for s in layer], return_exceptions=True)`.
- `run_one_step` catches all exceptions and returns a `status=failed` dict — never re-raises.
- Sub-session: `f"{session_id}__step_{step.id}"` → each step writes to its own session slot, eliminating blackboard read-after-write race conditions.

**Control flow**:
```
for each layer:
  results = await asyncio.gather(all steps in layer)
  for each result:
    if pending_approval → mark flag, break inner loop
    if exception / failed → record, continue (fail-open)
  if pending_approval → break outer loop
```

---

### `AgentPerfMetrics.record_parallel_steps(tenant_id, steps)`

```
Location: packages/agent/perf_metrics.py
```

Increments `_parallel_steps[tenant_id]` counter by `steps`.  
Exposed via `prometheus_text()` as:

```
# HELP agent_plan_parallel_steps_total Parallel plan steps dispatched
# TYPE agent_plan_parallel_steps_total counter
agent_plan_parallel_steps_total{tenant_id="..."} <N>
```

---

## 4. REST API Changes

None. `execute_plan_parallel` is called from `packages/agent/runner.py` (via `run_agent`) — no new routes needed. The existing `/internal/agent/run` endpoint benefits transparently when `auto_plan=true`.

---

## 5. Config / Settings Changes

None. No new settings fields are required.

> **Integration note for shared-file maintainer**: No changes to `apps/gateway/main.py`, `apps/gateway/settings.py`, `.env.example`, or `docs/roadmap.md` are needed for this issue.

---

## 6. Test Section

**File**: `tests/test_plan_parallel.py`  
**Test runner**: `python tests/test_plan_parallel.py` or `pytest tests/test_plan_parallel.py`

| # | Test Name | What It Verifies |
|---|---|---|
| 1 | `test_plan_execution_layers_empty` | Empty steps → `[]` |
| 2 | `test_plan_execution_layers_single` | Single step → `[[step]]` |
| 3 | `test_plan_execution_layers_no_deps` | All independent → 1 layer |
| 4 | `test_plan_execution_layers_linear` | s1→s2→s3 → 3 layers, 1 each |
| 5 | `test_plan_execution_layers_parallel` | s1→[s2,s3]→s4 → 3 layers |
| 6 | `test_plan_execution_layers_diamond_shape` | Same diamond via IDs |
| 7 | `test_plan_execution_layers_preserves_all_steps` | All steps in output exactly once |
| 8 | `test_execute_plan_parallel_simple` | 2 parallel steps → `plan_steps_completed=2` |
| 9 | `test_execute_plan_parallel_pending_approval_stops` | `pending_approval` stops next layer |
| 10 | `test_execute_plan_parallel_step_failure_continues` | Exception → fail-open, other step runs |
| 11 | `test_execute_plan_parallel_collects_tool_calls` | `tool_calls` aggregated from all steps |
| 12 | `test_execute_plan_parallel_sub_session_ids` | Sub-session IDs verified via `kwargs` |
| 13 | `test_execute_plan_parallel_metrics` | `record_parallel_steps` increments counter |
| 14 | `test_execute_plan_parallel_multi_layer` | 3-layer linear plan completes all 3 |
| 15 | `test_execute_plan_parallel_returns_correct_keys` | Return dict has all expected keys |
| 16 | `test_execute_plan_parallel_tool_calls_dict_format` | `dict` tool calls converted to `ToolCallRecord` |
| 17 | `test_record_parallel_steps_basic` | Counter accumulates correctly |
| 18 | `test_record_parallel_steps_zero_ignored` | Steps=0 skips increment |
| 19 | `test_prometheus_text_includes_parallel_counter` | Prometheus output includes new counter |

**Run result**: `Ran 19 tests in 0.004s — OK`

---

## 7. Code Navigation

```
packages/agent/
  planner.py                  ← plan_execution_layers(), execute_plan_parallel()
  perf_metrics.py             ← AgentPerfMetrics.record_parallel_steps()

tests/
  test_plan_parallel.py       ← 19 unit tests (all mock, no LLM)

eval/
  agent_planner_smoke.py      ← test_parallel_execution() appended
```

---

## 8. Known Limits

1. **Sub-session isolation**: Each step writes to an independent sub-session. The main session is **not updated** during parallel execution — caller must merge results if memory persistence is needed.
2. **No backpressure**: All steps in a layer are dispatched simultaneously. If a layer has hundreds of steps, all fire at once. A semaphore/concurrency limiter could be added in a future iteration.
3. **Result ordering**: `asyncio.gather` preserves order within a layer. The returned `final_message` is the last non-empty message encountered (deterministic within a single-threaded event loop).
4. **Cycle detection**: `plan_execution_layers` does not detect cycles — the existing `validate_plan` / `topological_sort` must be called before `plan_execution_layers`. Cycles will silently produce fewer layers than expected.
5. **`step_system_messages` only injected for `layer_index == 1`**: Multi-layer plans with system messages only inject them into the first step invocation.

---

## 9. Interview Talking Points

1. **Why BFS layers instead of full toposort?**  
   Full topological sort gives one valid serial order but loses parallelism information. BFS by indegree explicitly reveals which steps are independent at each frontier.

2. **Sub-session isolation rationale**  
   A shared blackboard session causes read-after-write races when two coroutines update `session.messages` concurrently. Using `{session_id}__step_{step_id}` gives each step a private namespace, making parallel execution safe without locks.

3. **Fail-open vs fail-fast trade-offs**  
   Fail-fast stops immediately on any error (better for correctness, worse for throughput). Fail-open maximises throughput for independent steps — a failed `calc` step doesn't prevent a concurrent `get_kb_snippet` from completing. The issue spec explicitly chose fail-open.

4. **`return_exceptions=True` in asyncio.gather**  
   Without this flag, the first exception cancels all pending coroutines and re-raises. With it, exceptions are returned as values alongside normal results, enabling per-step error handling.

5. **Prometheus counter design**  
   `agent_plan_parallel_steps_total` counts dispatched steps (not completed), so it tracks parallelism opportunity rather than success. Combine with `agent_plan_steps_total` to compute the parallelism ratio.
