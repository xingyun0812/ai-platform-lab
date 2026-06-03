from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from apps.gateway.settings import REPO_ROOT, get_settings


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: str
    bearer_token: str
    daily_request_quota: int  # -1 表示不限
    allowed_models: tuple[str, ...]
    allowed_tools: tuple[str, ...]  # 空表示可用全部注册工具


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


def load_tenants(path: Path | None = None) -> dict[str, TenantRecord]:
    """加载租户；allowed_models 为空表示不限制模型。支持 config/tenants.local.yaml 覆盖。"""
    settings = get_settings()
    base_path = path or settings.tenants_config_path
    raw = _tenant_map_from_file(base_path)
    local_path = base_path.parent / "tenants.local.yaml"
    if local_path.is_file():
        local_raw = _tenant_map_from_file(local_path)
        raw = _merge_tenant_dict(raw, local_raw)

    out: dict[str, TenantRecord] = {}
    for tenant_id, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"租户 {tenant_id} 配置必须为映射")
        token = cfg.get("bearer_token")
        if not token or not isinstance(token, str):
            raise ValueError(f"租户 {tenant_id} 缺少 bearer_token")
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
        out[str(tenant_id)] = TenantRecord(
            tenant_id=str(tenant_id),
            bearer_token=token,
            daily_request_quota=quota,
            allowed_models=allowed,
            allowed_tools=allowed_tools,
        )
    return out
