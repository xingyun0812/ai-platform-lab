from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from packages.platform import REPO_ROOT
from packages.platform import get_settings


@dataclass(frozen=True)
class RegionConfig:
    region_id: str
    qdrant_url: str
    data_zone: str
    allowed_tenant_zones: tuple[str, ...]


class RegionViolation(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache
def get_regions() -> dict[str, RegionConfig]:
    raw = _read_yaml(REPO_ROOT / "config" / "regions.yaml")
    regions_raw = raw.get("regions") or {}
    out: dict[str, RegionConfig] = {}
    if isinstance(regions_raw, dict):
        for rid, cfg in regions_raw.items():
            if not isinstance(cfg, dict):
                continue
            zones = cfg.get("allowed_tenant_zones") or []
            out[str(rid)] = RegionConfig(
                region_id=str(rid),
                qdrant_url=str(cfg.get("qdrant_url", get_settings().qdrant_url)),
                data_zone=str(cfg.get("data_zone", "GLOBAL")),
                allowed_tenant_zones=tuple(str(z) for z in zones) if isinstance(zones, list) else ("GLOBAL",),
            )
    return out


def default_region_id() -> str:
    raw = _read_yaml(REPO_ROOT / "config" / "regions.yaml")
    return str(raw.get("default_region", "cn-local"))


def resolve_region(
    *,
    header_region: str | None,
    tenant_home_region: str | None,
    tenant_data_zone: str,
) -> RegionConfig:
    regions = get_regions()
    rid = (header_region or tenant_home_region or default_region_id()).strip()
    cfg = regions.get(rid)
    if cfg is None:
        raise RegionViolation("REGION_UNKNOWN", f"未知 region: {rid}")
    zone = (tenant_data_zone or "GLOBAL").upper()
    region_zone = cfg.data_zone.upper()
    if zone != "GLOBAL" and region_zone != zone:
        raise RegionViolation(
            "DATA_RESIDENCY_VIOLATION",
            f"租户 data_zone={zone} 不可路由到 region={rid}（data_zone={region_zone}）",
        )
    allowed = {z.upper() for z in cfg.allowed_tenant_zones}
    if zone not in allowed and "GLOBAL" not in allowed and zone != "GLOBAL":
        raise RegionViolation(
            "DATA_RESIDENCY_VIOLATION",
            f"租户 data_zone={zone} 不在 region {rid} 允许列表 {sorted(allowed)}",
        )
    return cfg


def regions_payload() -> dict[str, Any]:
    regions = get_regions()
    return {
        "default_region": default_region_id(),
        "regions": [
            {
                "region_id": r.region_id,
                "qdrant_url": r.qdrant_url,
                "data_zone": r.data_zone,
                "allowed_tenant_zones": list(r.allowed_tenant_zones),
            }
            for r in regions.values()
        ],
    }
