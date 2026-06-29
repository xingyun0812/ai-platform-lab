"""Gateway 租户门面 — 实现已迁至 packages.tenant / packages.contracts。"""

from __future__ import annotations

from packages.contracts.tenant import TenantRecord
from packages.tenant.loader import load_tenants

__all__ = ["TenantRecord", "load_tenants"]
