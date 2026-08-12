from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.request_guards import check_rate_limit, check_token_budget
from apps.gateway.settings import get_settings
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.contracts.agent_schemas import AgentRunRequest

logger = logging.getLogger("ai_platform.gateway.computer_use")

router = APIRouter(prefix="/v1/agent", tags=["computer_use"])


def _require_tenant(
    x_tenant_id: str | None,
    authorization: str | None,
    tenants: dict[str, TenantRecord],
) -> TenantRecord | JSONResponse:
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except Exception as e:
        return json_error(401, "UNAUTHORIZED", str(e))


def _last_user_goal(messages: list) -> str | None:
    for m in reversed(messages):
        if getattr(m, "role", None) == "user":
            content = getattr(m, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


@router.post("/computer-use")
async def agent_computer_use(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase V: Computer Use Agent。"""
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant

    if not x_tenant_id or body.tenant_id.strip() != x_tenant_id.strip():
        return json_error(400, "TENANT_MISMATCH", "body.tenant_id 须与 X-Tenant-Id 一致")

    settings = get_settings()
    rate_err = check_rate_limit(tenant)
    if rate_err is not None:
        return rate_err
    budget_err = check_token_budget(tenant)
    if budget_err is not None:
        return budget_err

    task = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not task:
        return json_error(400, "INVALID_REQUEST", "Computer Use 需要 goal 或 user 消息")

    from packages.agent.computer_use import ComputerUseConfig as CUC_DC
    from packages.agent.computer_use import run_computer_use

    cfg = CUC_DC(max_steps=10)

    try:
        result = await run_computer_use(task=task, config=cfg, model=body.model)
    except Exception as e:
        logger.exception("agent_computer_use failed")
        return json_error(503, "COMPUTER_USE_ERROR", str(e))

    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.final_answer or "",
        "computer_use_result": result.to_dict(),
    })
