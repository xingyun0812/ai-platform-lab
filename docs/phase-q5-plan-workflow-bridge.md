# Phase Q5 — Plan to Workflow Bridge

**Issue**: [#120 — Q5 Plan to workflow bridge — export + adapter](https://github.com/xingyun0812/ai-platform-lab/issues/120)
**Branch**: `feat/issue-120-plan-workflow-bridge`
**Status**: Implemented

---

## Overview

Q5 bridges the gap between the Planner subsystem and the Orchestrator subsystem:

| Subsystem | Input | Output |
|---|---|---|
| **Planner** (`packages/agent/planner.py`) | User goal (natural language) | `AgentPlan` (goal + steps + depends_on) |
| **Orchestrator** (`packages/agent/orchestrator/`) | `config/workflows/*.yaml` | Executed node-edge workflow |

`plan_to_workflow` converts an `AgentPlan` to a workflow spec dict that matches the structure of `config/workflows/*.yaml`, enabling the Planner's output to be executed directly by the Orchestrator.

---

## Data Model Alignment

### `config/workflows/data_analysis.yaml` format

```yaml
workflows:
  - workflow_id: data-analysis-vertical
    name: "Data Analysis Vertical"
    description: "演示 sales 分析"
    created_by: system
    start_node: start
    end_node: end
    nodes:
      - node_id: web_search
        node_type: tool_call
        config:
          tool_name: web_search
          arguments:
            query: "${input.topic}"
    edges:
      - from_node: start
        to_node: web_search
```

### `plan_to_workflow` output format

```yaml
name: "<goal[:50]>"
description: "<full goal>"
nodes:
  - id: "<step.id>"
    type: "agent"
    config:
      description: "<step.description>"
      tool_hint: "<step.tool_hint | null>"
      agent_hint: "<step.agent_hint | null>"
edges:
  - from: "<dep_id>"
    to: "<step_id>"
metadata:
  generated_by: plan_to_workflow
  plan_steps: <int>
  source: AgentPlan
```

### Field mapping table

| `config/workflows/*.yaml` field | `plan_to_workflow` output field | Notes |
|---|---|---|
| `name` | `name` | Truncated to 50 chars from `goal` |
| `description` | `description` | Full `plan.goal` string |
| `nodes[].node_id` | `nodes[].id` | Directly from `step.id` |
| `nodes[].node_type` | `nodes[].type` | Always `"agent"` for plan-derived steps |
| `nodes[].config.tool_name` | `nodes[].config.tool_hint` | Renamed (hint, not binding) |
| `edges[].from_node` | `edges[].from` | From `step.depends_on` entries |
| `edges[].to_node` | `edges[].to` | The step that declares the dependency |
| _(not present)_ | `metadata` | Provenance tracking |

> **Note**: The `start_node` / `end_node` fields and synthetic `start`/`end` nodes present in hand-authored workflows are intentionally omitted in the bridge output. The Orchestrator can infer entry/exit nodes from the edge topology.

---

## Example — 2-Step Plan → YAML

### Input `AgentPlan`

```python
plan = AgentPlan(
    goal="分析 Q2 销售数据并生成报告",
    steps=[
        PlanStep(id="s1", description="从数据库获取 Q2 销售数据", tool_hint="sql_query", depends_on=[]),
        PlanStep(id="s2", description="生成销售分析报告", tool_hint=None, depends_on=["s1"]),
    ],
)
```

### Output YAML (`plan_to_workflow_yaml(plan)`)

```yaml
name: 分析 Q2 销售数据并生成报告
description: 分析 Q2 销售数据并生成报告
nodes:
- id: s1
  type: agent
  config:
    description: 从数据库获取 Q2 销售数据
    tool_hint: sql_query
    agent_hint: null
- id: s2
  type: agent
  config:
    description: 生成销售分析报告
    tool_hint: null
    agent_hint: null
edges:
- from: s1
  to: s2
metadata:
  generated_by: plan_to_workflow
  plan_steps: 2
  source: AgentPlan
```

---

## REST API

### `POST /v1/agent/plan/export`

Export an `AgentPlan` as an Orchestrator workflow YAML.

**Auth**: `X-Tenant-Id` + `Authorization: Bearer <token>`

**Request** (`application/json`):

```json
{
    "plan": {
        "goal": "分析 Q2 销售数据并生成报告",
        "steps": [
            {"id": "s1", "description": "获取数据", "depends_on": []},
            {"id": "s2", "description": "生成报告", "depends_on": ["s1"]}
        ]
    }
}
```

**Response** (`200 text/yaml`):

```yaml
name: 分析 Q2 销售数据并生成报告
description: 分析 Q2 销售数据并生成报告
nodes: ...
edges: ...
metadata: ...
```

**Error codes**:

| HTTP | code | Condition |
|---|---|---|
| 401 | `UNAUTHORIZED` | Missing / invalid auth headers |
| 422 | `MISSING_PLAN` | `plan` field absent from body |
| 422 | `INVALID_PLAN` | `plan` cannot be parsed as `AgentPlan` |
| 500 | `WORKFLOW_EXPORT_ERROR` | Unexpected serialization failure |

---

## Code Navigation

| File | Role |
|---|---|
| `packages/agent/plan_workflow.py` | Core bridge: `plan_to_workflow`, `workflow_to_yaml`, `plan_to_workflow_yaml` |
| `apps/gateway/agent/plan_workflow_routes.py` | FastAPI router: `POST /v1/agent/plan/export` |
| `tests/test_plan_workflow.py` | Unit tests (12 cases) |
| `packages/contracts/agent_schemas.py` | `AgentPlan`, `PlanStep` data models |
| `packages/agent/planner.py` | Planner that produces `AgentPlan` |
| `packages/agent/orchestrator/` | Orchestrator that consumes workflow specs |
| `config/workflows/data_analysis.yaml` | Reference workflow YAML format |

---

## Shared File Integration Instructions

> These changes must be applied to shared files by the integrating engineer.

### `apps/gateway/settings.py`

No new settings fields are required for Q5. The bridge is a pure function with no configuration.

### `apps/gateway/main.py`

Add the following import and `include_router` call:

```python
# Near the other agent router imports
from apps.gateway.agent.plan_workflow_routes import router as plan_workflow_router

# In the router registration section
app.include_router(plan_workflow_router)
```

### `.env.example`

No new environment variables required.

### `README.md`

Add under the **Phase Q** section:

```markdown
### Q5 — Plan to Workflow Bridge
Convert an `AgentPlan` to an Orchestrator-compatible workflow YAML.
- `POST /v1/agent/plan/export` — export plan as workflow YAML
- Module: `packages/agent/plan_workflow.py`
```

### `docs/roadmap.md`

Mark Q5 as complete:

```markdown
- [x] Q5 Plan to workflow bridge — `plan_to_workflow` + REST export
```

---

## Test Coverage

```
tests/test_plan_workflow.py  (12 test cases)
  TestPlanToWorkflowRequiredKeys     — output has name/description/nodes/edges/metadata
  TestPlanToWorkflowNodesCount       — nodes count == plan steps count
  TestPlanToWorkflowLinearEdges      — s1→s2 dep produces correct edge
  TestPlanToWorkflowNoDepsNoEdges    — no depends_on → empty edges list
  TestPlanToWorkflowNameFromGoal     — name truncated at 50 chars, description is full goal
  TestPlanToWorkflowNodeConfigFields — node.config has description/tool_hint/agent_hint
  TestWorkflowToYamlIsValidYaml      — yaml.safe_load parses output
  TestPlanToWorkflowYamlRoundtrip    — round-trip YAML contains expected nodes
  TestPlanToWorkflowMetadata         — metadata has generated_by/plan_steps/source
  TestPlanToWorkflowMultipleEdges    — diamond graph produces 4 edges
  TestPlanToWorkflowNodeOrder        — node order matches step order
  TestPlanExportRoute                — POST /v1/agent/plan/export returns text/yaml
```

---

## Known Limitations

1. **No `start`/`end` sentinel nodes**: Hand-authored workflows in `config/workflows/*.yaml` wrap nodes between `start` and `end` nodes. The bridge output omits these; the Orchestrator must handle workflows without explicit sentinels (it already infers entry/exit from topology).

2. **`type: agent` is hardcoded**: All plan steps map to `type: agent`. Future work could inspect `tool_hint` to map tool-call steps to `type: tool_call`.

3. **No cycle detection**: If the `AgentPlan` contains dependency cycles (which the Planner is supposed to prevent), the bridge will faithfully export them, and the Orchestrator will fail at execution time.

4. **No Orchestrator direct-execution integration**: Q5 produces a YAML spec but does not automatically enqueue it into the Orchestrator. A follow-up issue could add `POST /v1/agent/plan/run-as-workflow` to export + execute in one call.

---

## Interview Talking Points

- **Bridge pattern**: Rather than modifying either the Planner or the Orchestrator, Q5 introduces a thin adapter layer that converts between their native formats — following the _Bridge_ GoF pattern.
- **Format alignment**: The output dict mirrors the `config/workflows/*.yaml` schema (nodes, edges, metadata), making it immediately parseable by the Orchestrator YAML loader with minimal adaptation.
- **Idempotency**: `plan_to_workflow` is a pure function — same input always produces the same output. No state, no side effects.
- **Graceful compatibility**: The bridge does not require the Orchestrator to know anything about AgentPlans; the YAML it produces is indistinguishable from a hand-authored workflow.
- **Extensibility**: The `metadata.source` field enables future tools to distinguish bridge-generated workflows from hand-authored ones for observability or replay.
