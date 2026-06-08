from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.gateway.settings import get_settings
from packages.billing.db import get_billing_store


@dataclass(frozen=True)
class BudgetSnapshot:
    used_daily: int
    used_monthly: int
    remaining_daily: int | None
    remaining_monthly: int | None
    token_budget_daily: int
    token_budget_monthly: int


def _utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _utc_month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def get_budget_snapshot(
    tenant_id: str,
    *,
    token_budget_daily: int,
    token_budget_monthly: int,
) -> BudgetSnapshot | None:
    store = get_billing_store(get_settings().database_url)
    if store is None:
        return None
    used_daily = store.sum_tokens(tenant_id, since=_utc_day_start())
    used_monthly = store.sum_tokens(tenant_id, since=_utc_month_start())
    remaining_daily = (
        None if token_budget_daily < 0 else max(0, token_budget_daily - used_daily)
    )
    remaining_monthly = (
        None if token_budget_monthly < 0 else max(0, token_budget_monthly - used_monthly)
    )
    return BudgetSnapshot(
        used_daily=used_daily,
        used_monthly=used_monthly,
        remaining_daily=remaining_daily,
        remaining_monthly=remaining_monthly,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )


def is_budget_exceeded(
    tenant_id: str,
    *,
    token_budget_daily: int,
    token_budget_monthly: int,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """返回 (是否超限, 错误码, detail)。"""
    snap = get_budget_snapshot(
        tenant_id,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
    if snap is None:
        return False, None, None
    if token_budget_daily >= 0 and snap.used_daily >= token_budget_daily:
        return (
            True,
            "BUDGET_EXCEEDED",
            {
                "scope": "daily",
                "used_tokens": snap.used_daily,
                "token_budget": token_budget_daily,
            },
        )
    if token_budget_monthly >= 0 and snap.used_monthly >= token_budget_monthly:
        return (
            True,
            "BUDGET_EXCEEDED",
            {
                "scope": "monthly",
                "used_tokens": snap.used_monthly,
                "token_budget": token_budget_monthly,
            },
        )
    return False, None, None


def budget_platform_meta(snap: BudgetSnapshot | None, usage_total: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if usage_total is not None:
        meta["total_tokens"] = usage_total
    if snap is None:
        return meta
    meta["used_tokens_daily"] = snap.used_daily
    meta["used_tokens_monthly"] = snap.used_monthly
    if snap.remaining_daily is not None:
        meta["budget_remaining_daily"] = snap.remaining_daily
    if snap.remaining_monthly is not None:
        meta["budget_remaining_monthly"] = snap.remaining_monthly
    return meta
