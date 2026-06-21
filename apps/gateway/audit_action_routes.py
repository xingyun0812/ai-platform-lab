"""动作分级审计 REST API — Phase I #42

路由前缀：/internal/audit-actions

接口：
    GET    /internal/audit-actions/classifications              列出所有工具分类
    GET    /internal/audit-actions/classifications/{tool_name}  获取单个工具分类
    POST   /internal/audit-actions/classifications              注册分类（admin）
    PATCH  /internal/audit-actions/classifications/{tool_name}  更新分类（admin）
    DELETE /internal/audit-actions/classifications/{tool_name}  删除分类（admin）
    POST   /internal/audit-actions/classify                     分类工具调用
    GET    /internal/audit-actions/actions                      列出审计记录
    GET    /internal/audit-actions/actions/destructive          列出 destructive 记录
    GET    /internal/audit-actions/actions/{entry_id}           获取审计记录详情
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/audit-actions", tags=["audit-actions"])


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _resolve(x_tenant_id: str | None, authorization: str | None) -> TenantRecord | JSONResponse:
    tenants = load_tenants()
    try:
        return resolve_tenant(x_tenant_id, authorization, tenants)
    except HTTPException as e:
        return json_error(int(e.status_code), "UNAUTHORIZED", str(e.detail))


def _require_admin(tenant: TenantRecord) -> JSONResponse | None:
    if not can_patch_tenant_limits(tenant.role):
        return json_error(403, "FORBIDDEN", "需要 platform_admin 角色")
    return None


def _classifier_or_503():
    from packages.audit.action_levels import get_classifier

    c = get_classifier()
    if c is None:
        return json_error(503, "AUDIT_ACTIONS_DISABLED", "audit-actions 未初始化")
    return c


def _logger_or_503():
    from packages.audit.action_logger import get_action_logger

    lg = get_action_logger()
    if lg is None:
        return json_error(503, "AUDIT_LOGGER_DISABLED", "action_logger 未初始化")
    return lg


# ---------------------------------------------------------------------------
# Pydantic 请求体
# ---------------------------------------------------------------------------

class ClassificationCreateRequest(BaseModel):
    tool_name: str = Field(..., min_length=1)
    action_level: str = Field(..., description="read_only | write | destructive | network | unknown")
    requires_approval: bool = False
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClassificationUpdateRequest(BaseModel):
    action_level: str | None = None
    requires_approval: bool | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


class ClassifyRequest(BaseModel):
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 分类管理
# ---------------------------------------------------------------------------

@router.get("/classifications")
async def list_classifications(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier
    items = classifier.list_classifications()
    return JSONResponse(
        {
            "classifications": [c.to_dict() for c in items],
            "total": len(items),
        }
    )


@router.get("/classifications/{tool_name}")
async def get_classification(
    tool_name: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier
    cls_ = classifier.get_classification(tool_name)
    if cls_ is None:
        return json_error(404, "NOT_FOUND", f"工具 {tool_name} 无分类记录")
    return JSONResponse(cls_.to_dict())


@router.post("/classifications")
async def create_classification(
    body: ClassificationCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier

    from packages.audit.action_levels import ActionLevel, ToolActionClassification

    if not ActionLevel.is_valid(body.action_level):
        return json_error(400, "INVALID_ACTION_LEVEL", f"无效的 action_level: {body.action_level}")

    cls_ = ToolActionClassification(
        tool_name=body.tool_name,
        action_level=body.action_level,
        requires_approval=body.requires_approval,
        description=body.description,
        metadata=body.metadata,
    )
    classifier.register_classification(cls_)
    return JSONResponse(cls_.to_dict(), status_code=201)


@router.patch("/classifications/{tool_name}")
async def update_classification(
    tool_name: str,
    body: ClassificationUpdateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier

    from packages.audit.action_levels import ActionLevel, ToolActionClassification

    existing = classifier.get_classification(tool_name)
    if existing is None:
        return json_error(404, "NOT_FOUND", f"工具 {tool_name} 无分类记录")

    new_level = body.action_level if body.action_level is not None else existing.action_level
    if not ActionLevel.is_valid(new_level):
        return json_error(400, "INVALID_ACTION_LEVEL", f"无效的 action_level: {new_level}")

    updated = ToolActionClassification(
        tool_name=tool_name,
        action_level=new_level,
        requires_approval=body.requires_approval if body.requires_approval is not None else existing.requires_approval,
        description=body.description if body.description is not None else existing.description,
        metadata=body.metadata if body.metadata is not None else existing.metadata,
    )
    classifier.register_classification(updated)
    return JSONResponse(updated.to_dict())


@router.delete("/classifications/{tool_name}")
async def delete_classification(
    tool_name: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier
    ok = classifier.remove_classification(tool_name)
    if not ok:
        return json_error(404, "NOT_FOUND", f"工具 {tool_name} 无分类记录")
    return JSONResponse({"tool_name": tool_name, "deleted": True})


# ---------------------------------------------------------------------------
# 分类工具调用
# ---------------------------------------------------------------------------

@router.post("/classify")
async def classify_tool_call(
    body: ClassifyRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    classifier = _classifier_or_503()
    if isinstance(classifier, JSONResponse):
        return classifier
    action_level = classifier.classify(body.tool_name, body.arguments)
    requires_approval = classifier.requires_approval(body.tool_name)
    cls_ = classifier.get_classification(body.tool_name)
    return JSONResponse(
        {
            "tool_name": body.tool_name,
            "action_level": action_level,
            "requires_approval": requires_approval,
            "source": "registry" if cls_ is not None else "heuristic",
        }
    )


# ---------------------------------------------------------------------------
# 审计记录查询
# ---------------------------------------------------------------------------

@router.get("/actions")
async def list_actions(
    tenant_id: str | None = None,
    action_level: str | None = None,
    limit: int = 50,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    logger = _logger_or_503()
    if isinstance(logger, JSONResponse):
        return logger
    query_tenant = tenant_id or tenant.tenant_id
    entries = await logger.list_actions(
        query_tenant, action_level=action_level, limit=min(limit, 500)
    )
    return JSONResponse(
        {
            "actions": [e.to_dict() for e in entries],
            "total": len(entries),
        }
    )


@router.get("/actions/destructive")
async def list_destructive_actions(
    tenant_id: str | None = None,
    limit: int = 50,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    logger = _logger_or_503()
    if isinstance(logger, JSONResponse):
        return logger
    query_tenant = tenant_id or tenant.tenant_id
    entries = await logger.list_destructive_actions(query_tenant, limit=min(limit, 500))
    return JSONResponse(
        {
            "actions": [e.to_dict() for e in entries],
            "total": len(entries),
        }
    )


@router.get("/actions/{entry_id}")
async def get_action(
    entry_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    logger = _logger_or_503()
    if isinstance(logger, JSONResponse):
        return logger
    entry = await logger.get_action(entry_id)
    if entry is None:
        return json_error(404, "NOT_FOUND", f"审计记录 {entry_id} 不存在")
    return JSONResponse(entry.to_dict())
