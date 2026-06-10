from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from apps.gateway.settings import REPO_ROOT


@lru_cache(maxsize=1)
def load_tool_catalog() -> dict[str, dict[str, Any]]:
    path = REPO_ROOT / "config" / "tools_marketplace.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tools = data.get("tools") if isinstance(data, dict) else {}
    return {str(k): v for k, v in tools.items()} if isinstance(tools, dict) else {}


def tool_requires_hitl(tool_name: str) -> bool:
    meta = load_tool_catalog().get(tool_name) or {}
    if not isinstance(meta, dict):
        return False
    risk = str(meta.get("risk_level") or "low").lower()
    return risk == "high"
