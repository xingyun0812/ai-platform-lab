"""租户契约 — packages 可读 TenantRecord，不依赖 apps.gateway。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: str
    bearer_token: str
    daily_request_quota: int  # -1 表示不限
    allowed_models: tuple[str, ...]
    allowed_tools: tuple[str, ...]  # 空表示可用全部注册工具
    default_model: str | None  # 租户默认模型或别名
    rate_limit_rps: float
    rate_limit_burst: int
    token_budget_daily: int  # -1 表示不限（UTC 日切）
    token_budget_monthly: int  # -1 表示不限（自然月）
    home_region: str | None = None  # Phase C：默认 region
    data_zone: str = "GLOBAL"  # Phase C：数据驻留区 CN/EU/GLOBAL
    role: str = "developer"  # Phase D：viewer | developer | tenant_admin | platform_admin
