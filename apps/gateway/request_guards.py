from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from apps.gateway.http_utils import json_error
from apps.gateway.rate_limit import RateLimitPolicy, get_rate_limiter
from apps.gateway.tenants import TenantRecord
from packages.billing.budget import is_budget_exceeded
from packages.platform import is_model_allowed


def check_token_budget(tenant: TenantRecord) -> JSONResponse | None:
    exceeded, code, detail = is_budget_exceeded(
        tenant.tenant_id,
        token_budget_daily=tenant.token_budget_daily,
        token_budget_monthly=tenant.token_budget_monthly,
    )
    if not exceeded:
        return None
    return json_error(
        429,
        code or "BUDGET_EXCEEDED",
        "租户 token 预算已用尽",
        detail={**(detail or {}), "tenant_id": tenant.tenant_id},
    )


def check_rate_limit(tenant: TenantRecord) -> JSONResponse | None:
    limiter = get_rate_limiter()
    policy = RateLimitPolicy(rps=tenant.rate_limit_rps, burst=tenant.rate_limit_burst)
    if not limiter.try_acquire(tenant.tenant_id, policy):
        retry_after = round(limiter.retry_after_seconds(tenant.tenant_id, policy), 2)
        return json_error(
            429,
            "RATE_LIMIT_EXCEEDED",
            "租户请求速率超限（令牌桶）",
            detail={
                "tenant_id": tenant.tenant_id,
                "rate_limit_rps": tenant.rate_limit_rps,
                "rate_limit_burst": tenant.rate_limit_burst,
                "retry_after_seconds": retry_after,
            },
        )
    return None


def check_model_allowed(
    tenant: TenantRecord,
    requested_model: str | None,
) -> tuple[JSONResponse | None, str]:
    allowed, resolved = is_model_allowed(
        requested_model,
        tenant_default=tenant.default_model,
        allowed_models=tenant.allowed_models,
    )
    if not allowed:
        return (
            json_error(
                403,
                "MODEL_NOT_ALLOWED",
                f"模型不在租户白名单: {requested_model or tenant.default_model or 'default'}",
                detail={
                    "allowed_models": list(tenant.allowed_models),
                    "resolved_model": resolved,
                },
            ),
            resolved,
        )
    return None, resolved


def upstream_route_detail(result_meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result_meta.items() if v is not None}
