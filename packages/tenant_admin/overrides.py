from __future__ import annotations

import json
from typing import Any

from packages.platform import REPO_ROOT

OVERRIDES_PATH = REPO_ROOT / "data" / "tenant_overrides.json"


def _read() -> dict[str, Any]:
    if not OVERRIDES_PATH.is_file():
        return {}
    data = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, Any]) -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_tenant_override(tenant_id: str) -> dict[str, Any]:
    all_data = _read()
    cfg = all_data.get(tenant_id)
    return cfg if isinstance(cfg, dict) else {}


def merge_tenant_overrides(raw_tenants: dict[str, Any]) -> dict[str, Any]:
    overrides = _read()
    out = dict(raw_tenants)
    for tid, patch in overrides.items():
        if not isinstance(patch, dict):
            continue
        if tid in out and isinstance(out[tid], dict):
            merged = {**out[tid], **patch}
            if "allowed_tools" in patch and isinstance(patch["allowed_tools"], list):
                base = out[tid].get("allowed_tools") or []
                if isinstance(base, list):
                    merged["allowed_tools"] = sorted(set(base) | set(patch["allowed_tools"]))
            out[tid] = merged
        else:
            out[tid] = patch
    return out


def patch_tenant_limits(tenant_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "daily_request_quota",
        "token_budget_daily",
        "token_budget_monthly",
        "rate_limit_rps",
        "rate_limit_burst",
        "allowed_tools",
    }
    filtered = {k: v for k, v in patch.items() if k in allowed_keys}
    all_data = _read()
    current = all_data.get(tenant_id, {})
    if not isinstance(current, dict):
        current = {}
    all_data[tenant_id] = {**current, **filtered}
    _write(all_data)
    return all_data[tenant_id]
