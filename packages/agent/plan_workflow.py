"""packages/agent/plan_workflow.py — Phase Q Q5 Plan to workflow bridge.

Converts an AgentPlan (goal + steps + depends_on) into an Orchestrator-compatible
workflow spec (dict / YAML), aligning with the format in config/workflows/*.yaml.
"""

from __future__ import annotations

from typing import Any

from packages.contracts.agent_schemas import AgentPlan

# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

_MAX_NAME_LEN = 50


def plan_to_workflow(plan: AgentPlan) -> dict[str, Any]:
    """将 AgentPlan 转换为 Orchestrator workflow spec。

    输出格式与 config/workflows/*.yaml 对齐：

    .. code-block:: yaml

        name: "<goal 前 50 字>"
        description: "<goal>"
        nodes:
          - id: "<step.id>"
            type: "agent"
            config:
              description: "<step.description>"
              tool_hint: "<step.tool_hint or null>"
              agent_hint: "<step.agent_hint or null>"
        edges:
          - from: "<dep_id>"
            to: "<step_id>"
        metadata:
          generated_by: plan_to_workflow
          plan_steps: <len(steps)>
          source: AgentPlan

    Args:
        plan: AgentPlan instance with goal and steps.

    Returns:
        A dict representing the workflow spec.
    """
    name = plan.goal[:_MAX_NAME_LEN]

    nodes: list[dict[str, Any]] = []
    for step in plan.steps:
        nodes.append(
            {
                "id": step.id,
                "type": "agent",
                "config": {
                    "description": step.description,
                    "tool_hint": step.tool_hint,
                    "agent_hint": step.agent_hint,
                },
            }
        )

    edges: list[dict[str, str]] = []
    for step in plan.steps:
        for dep_id in step.depends_on:
            edges.append({"from": dep_id, "to": step.id})

    return {
        "name": name,
        "description": plan.goal,
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated_by": "plan_to_workflow",
            "plan_steps": len(plan.steps),
            "source": "AgentPlan",
        },
    }


def workflow_to_yaml(workflow: dict[str, Any]) -> str:
    """将 workflow dict 序列化为 YAML 字符串。

    Args:
        workflow: Workflow spec dict (output of plan_to_workflow).

    Returns:
        YAML-formatted string.
    """
    import yaml  # PyYAML is already in project deps

    return yaml.dump(workflow, allow_unicode=True, default_flow_style=False, sort_keys=False)


def plan_to_workflow_yaml(plan: AgentPlan) -> str:
    """一步转换：AgentPlan → YAML 字符串。

    Convenience wrapper combining plan_to_workflow + workflow_to_yaml.

    Args:
        plan: AgentPlan instance.

    Returns:
        YAML string of the corresponding workflow spec.
    """
    return workflow_to_yaml(plan_to_workflow(plan))
