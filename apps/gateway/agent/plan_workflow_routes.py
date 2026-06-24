"""apps/gateway/agent/plan_workflow_routes.py — Plan workflow export API.

Routes:
  POST /v1/agent/plan/export  — Export an AgentPlan as a workflow YAML
                                 Body: {"plan": {...AgentPlan JSON...}}
                                 Response: 200 text/yaml

The endpoint is intentionally lightweight and requires no external services.
Auth uses the standard Bearer token / X-Tenant-Id headers.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants

logger = logging.getLogger("ai_platform.gateway.agent.plan_workflow")

router = APIRouter(prefix="/v1/agent/plan", tags=["plan-workflow"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            return json_error(int(exc.status_code), "UNAUTHORIZED", str(exc.detail))
        return json_error(401, "UNAUTHORIZED", str(exc))


def _parse_plan(plan_data: Any):  # type: ignore[return]
    """Parse raw dict into AgentPlan, returning (plan, error_response)."""
    from packages.contracts.agent_schemas import AgentPlan

    try:
        if isinstance(plan_data, dict):
            return AgentPlan(**plan_data), None
        return None, json_error(422, "INVALID_PLAN", "plan must be a JSON object")
    except Exception as exc:
        return None, json_error(422, "INVALID_PLAN", f"Cannot parse plan: {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/export")
async def export_plan_as_workflow(
    body: dict[str, Any],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """将 AgentPlan 导出为 Orchestrator-compatible workflow YAML。

    **Request body** (JSON):

    .. code-block:: json

        {
            "plan": {
                "goal": "分析 Q2 销售数据",
                "steps": [
                    {"id": "s1", "description": "获取数据", "depends_on": []},
                    {"id": "s2", "description": "分析数据", "depends_on": ["s1"]}
                ]
            }
        }

    **Response**:
    - ``200 text/yaml`` — workflow YAML content
    - ``401`` — missing / invalid auth
    - ``422`` — invalid plan body
    """
    tenants = load_tenants()
    caller = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(caller, JSONResponse):
        return caller

    plan_data = body.get("plan")
    if plan_data is None:
        return json_error(422, "MISSING_PLAN", "Request body must contain a 'plan' field")

    plan, err = _parse_plan(plan_data)
    if err is not None:
        return err

    try:
        from packages.agent.plan_workflow import plan_to_workflow_yaml

        yaml_content = plan_to_workflow_yaml(plan)
    except Exception as exc:
        logger.exception("plan_to_workflow_yaml failed: %s", exc)
        return json_error(500, "WORKFLOW_EXPORT_ERROR", f"Export failed: {exc}")

    return Response(content=yaml_content, media_type="text/yaml")
