"""租户 YAML 加载 — packages 层，依赖 packages.platform 而非 apps.gateway。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from packages.contracts.tenant import TenantRecord
from packages.platform import get_settings


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML 根节点须为映射: {path}")
    return data


def _tenant_map_from_file(path: Path) -> dict[str, Any]:
    data = _read_yaml(path)
    tenants = data.get("tenants")
    if not isinstance(tenants, dict):
        raise ValueError(f"缺少 tenants 映射: {path}")
    return tenants


def _merge_tenant_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for tid, cfg in override.items():
        if tid in out and isinstance(out[tid], dict) and isinstance(cfg, dict):
            out[tid] = {**out[tid], **cfg}
        else:
            out[tid] = cfg
    return out


def _defaults_from_file(path: Path) -> dict[str, Any]:
    data = _read_yaml(path)
    defaults = data.get("defaults")
    return defaults if isinstance(defaults, dict) else {}


def load_tenants(path: Path | None = None) -> dict[str, TenantRecord]:
    """加载租户；allowed_models 为空表示不限制模型。支持 config/tenants.local.yaml 覆盖。"""
    settings = get_settings()
    base_path = path or settings.tenants_config_path
    file_defaults = _defaults_from_file(base_path)
    default_rps = float(file_defaults.get("rate_limit_rps", settings.default_rate_limit_rps))
    default_burst = int(file_defaults.get("rate_limit_burst", settings.default_rate_limit_burst))
    raw = _tenant_map_from_file(base_path)
    local_path = base_path.parent / "tenants.local.yaml"
    if local_path.is_file():
        local_raw = _tenant_map_from_file(local_path)
        local_defaults = _defaults_from_file(local_path)
        if isinstance(local_defaults.get("rate_limit_rps"), (int, float)):
            default_rps = float(local_defaults["rate_limit_rps"])
        if isinstance(local_defaults.get("rate_limit_burst"), int):
            default_burst = int(local_defaults["rate_limit_burst"])
        raw = _merge_tenant_dict(raw, local_raw)

    from packages.tenant_admin.overrides import merge_tenant_overrides

    raw = merge_tenant_overrides(raw)

    out: dict[str, TenantRecord] = {}
    for tenant_id, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"租户 {tenant_id} 配置必须为映射")
        secret_ref = cfg.get("bearer_secret_ref")
        plain_token = cfg.get("bearer_token")
        token: str | None = None
        if isinstance(secret_ref, str) and secret_ref.strip():
            from packages.secrets.provider import resolve_secret

            fallback = plain_token if isinstance(plain_token, str) else None
            token = resolve_secret(secret_ref, fallback=fallback)
        elif isinstance(plain_token, str) and plain_token.strip():
            token = plain_token
        if not token:
            raise ValueError(f"租户 {tenant_id} 须配置 bearer_token 或 bearer_secret_ref")
        quota = cfg.get("daily_request_quota", 0)
        if not isinstance(quota, int):
            raise ValueError(f"租户 {tenant_id} daily_request_quota 须为整数")
        models = cfg.get("allowed_models")
        if models is None:
            allowed: tuple[str, ...] = ()
        elif isinstance(models, list):
            allowed = tuple(str(m) for m in models)
        else:
            raise ValueError(f"租户 {tenant_id} allowed_models 须为列表或为空")
        tools_cfg = cfg.get("allowed_tools")
        if tools_cfg is None:
            allowed_tools: tuple[str, ...] = ()
        elif isinstance(tools_cfg, list):
            allowed_tools = tuple(str(t) for t in tools_cfg)
        else:
            raise ValueError(f"租户 {tenant_id} allowed_tools 须为列表或为空")
        default_model = cfg.get("default_model")
        if default_model is not None and not isinstance(default_model, str):
            raise ValueError(f"租户 {tenant_id} default_model 须为字符串或省略")
        rps = cfg.get("rate_limit_rps", default_rps)
        burst = cfg.get("rate_limit_burst", default_burst)
        if not isinstance(rps, (int, float)) or rps < 0:
            raise ValueError(f"租户 {tenant_id} rate_limit_rps 须为非负数字")
        if not isinstance(burst, int) or burst < 0:
            raise ValueError(f"租户 {tenant_id} rate_limit_burst 须为非负整数")
        token_daily = cfg.get("token_budget_daily", -1)
        token_monthly = cfg.get("token_budget_monthly", -1)
        if not isinstance(token_daily, int):
            raise ValueError(f"租户 {tenant_id} token_budget_daily 须为整数")
        if not isinstance(token_monthly, int):
            raise ValueError(f"租户 {tenant_id} token_budget_monthly 须为整数")
        home_region = cfg.get("home_region")
        if home_region is not None and not isinstance(home_region, str):
            raise ValueError(f"租户 {tenant_id} home_region 须为字符串或省略")
        data_zone = cfg.get("data_zone", "GLOBAL")
        if not isinstance(data_zone, str):
            raise ValueError(f"租户 {tenant_id} data_zone 须为字符串")
        role = cfg.get("role", "developer")
        if not isinstance(role, str):
            raise ValueError(f"租户 {tenant_id} role 须为字符串")
        if tenant_id == "admin" and role == "developer":
            role = "platform_admin"
        out[str(tenant_id)] = TenantRecord(
            tenant_id=str(tenant_id),
            bearer_token=token,
            daily_request_quota=quota,
            allowed_models=allowed,
            allowed_tools=allowed_tools,
            default_model=str(default_model) if default_model else None,
            rate_limit_rps=float(rps),
            rate_limit_burst=int(burst),
            token_budget_daily=token_daily,
            token_budget_monthly=token_monthly,
            home_region=str(home_region) if home_region else None,
            data_zone=str(data_zone),
            role=str(role),
        )
    return out
