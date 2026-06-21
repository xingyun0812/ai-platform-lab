"""PII 脱敏 + 内容安全 REST API — Phase I #43

路由前缀：/internal/pii

接口：
    GET    /internal/pii/patterns                   列出所有 PII 模式
    POST   /internal/pii/patterns                   注册模式（admin）
    DELETE /internal/pii/patterns/{pattern_id}      删除模式（admin）
    GET    /internal/pii/policies                   列出所有脱敏策略
    POST   /internal/pii/policies                   注册策略（admin）
    POST   /internal/pii/detect                     检测 PII
    POST   /internal/pii/redact                     脱敏文本
    POST   /internal/pii/safety                     内容安全检查
    POST   /internal/pii/process                    完整流水线
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from apps.gateway.http_utils import json_error, resolve_tenant
from apps.gateway.tenants import TenantRecord, load_tenants
from packages.auth.rbac import can_patch_tenant_limits

router = APIRouter(prefix="/internal/pii", tags=["pii"])


# ---------------------------------------------------------------------------
# 辅助函数
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


def _get_service_or_503():
    from packages.pii.service import get_pii_service

    svc = get_pii_service()
    if svc is None:
        return json_error(503, "PII_DISABLED", "PII_SERVICE_ENABLED=false 或未初始化")
    return svc


def _get_detector_or_503():
    from packages.pii.detectors import get_detector

    det = get_detector()
    if det is None:
        return json_error(503, "PII_DISABLED", "PII 检测器未初始化")
    return det


def _get_redactor_or_503():
    from packages.pii.redactor import get_redactor

    red = get_redactor()
    if red is None:
        return json_error(503, "PII_DISABLED", "PII 脱敏器未初始化")
    return red


# ---------------------------------------------------------------------------
# Pydantic 请求体
# ---------------------------------------------------------------------------


class PatternCreateRequest(BaseModel):
    pattern_id: str = Field(..., min_length=1)
    name: str
    regex: str
    entity_type: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    redaction_template: str = "[REDACTED]"
    enabled: bool = True


class PolicyCreateRequest(BaseModel):
    policy_id: str = Field(..., min_length=1)
    entity_types: list[str] = Field(default_factory=list)
    action: str = Field(..., description="redact | mask | hash | block")
    mask_char: str = "*"
    keep_first: int = 2
    keep_last: int = 2
    hash_salt: str = ""


class DetectRequest(BaseModel):
    text: str


class RedactRequest(BaseModel):
    text: str
    policy_id: str = "default"


class SafetyRequest(BaseModel):
    text: str


class ProcessRequest(BaseModel):
    text: str
    policy_id: str = "default"
    check_safety: bool = True


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/patterns")
async def list_patterns(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    det = _get_detector_or_503()
    if isinstance(det, JSONResponse):
        return det
    patterns = det.list_patterns()
    return JSONResponse({"patterns": [p.to_dict() for p in patterns], "count": len(patterns)})


@router.post("/patterns")
async def create_pattern(
    body: PatternCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    det = _get_detector_or_503()
    if isinstance(det, JSONResponse):
        return det

    from packages.pii.detectors import PIIPattern

    pattern = PIIPattern(
        pattern_id=body.pattern_id,
        name=body.name,
        regex=body.regex,
        entity_type=body.entity_type,
        confidence=body.confidence,
        redaction_template=body.redaction_template,
        enabled=body.enabled,
    )
    det.register_pattern(pattern)
    return JSONResponse(pattern.to_dict(), status_code=201)


@router.delete("/patterns/{pattern_id}")
async def delete_pattern(
    pattern_id: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    det = _get_detector_or_503()
    if isinstance(det, JSONResponse):
        return det
    ok = det.remove_pattern(pattern_id)
    if not ok:
        return json_error(404, "NOT_FOUND", f"Pattern {pattern_id} 不存在")
    return JSONResponse({"pattern_id": pattern_id, "deleted": True})


@router.get("/policies")
async def list_policies(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    red = _get_redactor_or_503()
    if isinstance(red, JSONResponse):
        return red
    policies = red.list_policies()
    return JSONResponse({"policies": [p.to_dict() for p in policies], "count": len(policies)})


@router.post("/policies")
async def create_policy(
    body: PolicyCreateRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    err = _require_admin(tenant)
    if err is not None:
        return err
    red = _get_redactor_or_503()
    if isinstance(red, JSONResponse):
        return red

    if body.action not in ("redact", "mask", "hash", "block"):
        return json_error(400, "INVALID_ACTION", "action 必须是 redact/mask/hash/block")

    from packages.pii.redactor import RedactionPolicy

    policy = RedactionPolicy(
        policy_id=body.policy_id,
        entity_types=body.entity_types,
        action=body.action,
        mask_char=body.mask_char,
        keep_first=body.keep_first,
        keep_last=body.keep_last,
        hash_salt=body.hash_salt,
    )
    red.register_policy(policy)
    return JSONResponse(policy.to_dict(), status_code=201)


@router.post("/detect")
async def detect_pii(
    body: DetectRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _get_service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    matches = await svc.detect_pii(body.text)
    return JSONResponse({"matches": [m.to_dict() for m in matches], "count": len(matches)})


@router.post("/redact")
async def redact_text(
    body: RedactRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _get_service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    result = await svc.redact_pii(body.text, policy_id=body.policy_id)
    return JSONResponse(result.to_dict())


@router.post("/safety")
async def check_safety(
    body: SafetyRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _get_service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    result = await svc.check_safety(body.text)
    return JSONResponse(result.to_dict())


@router.post("/process")
async def process(
    body: ProcessRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    tenant = _resolve(x_tenant_id, authorization)
    if isinstance(tenant, JSONResponse):
        return tenant
    svc = _get_service_or_503()
    if isinstance(svc, JSONResponse):
        return svc
    result = await svc.process(
        body.text, policy_id=body.policy_id, check_safety=body.check_safety
    )
    return JSONResponse(result)
