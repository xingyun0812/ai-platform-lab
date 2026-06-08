from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import yaml

from apps.gateway.settings import REPO_ROOT
from packages.tenant_admin.overrides import patch_tenant_limits

REQUESTS_PATH = REPO_ROOT / "data" / "tool_requests.json"


class ToolRequestStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


@dataclass
class ToolRequest:
    request_id: str
    tenant_id: str
    tool_name: str
    status: ToolRequestStatus
    created_at: str
    updated_at: str
    reviewer: str | None = None


def _load_catalog() -> dict[str, dict[str, Any]]:
    path = REPO_ROOT / "config" / "tools_marketplace.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tools = data.get("tools") if isinstance(data, dict) else {}
    return {str(k): v for k, v in tools.items()} if isinstance(tools, dict) else {}


def catalog_payload() -> dict[str, Any]:
    catalog = _load_catalog()
    return {
        "tools": [
            {"name": name, **meta}
            for name, meta in catalog.items()
            if isinstance(meta, dict)
        ]
    }


def _read_requests() -> list[dict[str, Any]]:
    if not REQUESTS_PATH.is_file():
        return []
    data = json.loads(REQUESTS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _write_requests(rows: list[dict[str, Any]]) -> None:
    REQUESTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REQUESTS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def create_tool_request(*, tenant_id: str, tool_name: str) -> ToolRequest:
    catalog = _load_catalog()
    if tool_name not in catalog:
        raise ValueError(f"工具不在市场目录: {tool_name}")
    now = datetime.now(UTC).isoformat()
    req = ToolRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        tool_name=tool_name,
        status=ToolRequestStatus.pending,
        created_at=now,
        updated_at=now,
    )
    rows = _read_requests()
    rows.append(asdict(req))
    _write_requests(rows)
    return req


def list_tool_requests(*, tenant_id: str | None = None) -> list[ToolRequest]:
    out: list[ToolRequest] = []
    for row in _read_requests():
        if tenant_id and row.get("tenant_id") != tenant_id:
            continue
        out.append(
            ToolRequest(
                request_id=str(row["request_id"]),
                tenant_id=str(row["tenant_id"]),
                tool_name=str(row["tool_name"]),
                status=ToolRequestStatus(str(row["status"])),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                reviewer=row.get("reviewer"),
            )
        )
    return out


def _update_request(request_id: str, *, status: ToolRequestStatus, reviewer: str) -> ToolRequest | None:
    rows = _read_requests()
    found: ToolRequest | None = None
    for row in rows:
        if row.get("request_id") != request_id:
            continue
        row["status"] = status.value
        row["reviewer"] = reviewer
        row["updated_at"] = datetime.now(UTC).isoformat()
        found = ToolRequest(
            request_id=str(row["request_id"]),
            tenant_id=str(row["tenant_id"]),
            tool_name=str(row["tool_name"]),
            status=status,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            reviewer=reviewer,
        )
        break
    if found is None:
        return None
    _write_requests(rows)
    return found


def approve_tool_request(request_id: str, *, reviewer: str) -> ToolRequest | None:
    req = _update_request(request_id, status=ToolRequestStatus.approved, reviewer=reviewer)
    if req is None:
        return None
    from apps.gateway.tenants import load_tenants

    tenants = load_tenants()
    base = list(tenants[req.tenant_id].allowed_tools) if req.tenant_id in tenants else []
    tools = sorted(set(base) | set(get_tenant_override_tools(req.tenant_id)) | {req.tool_name})
    patch_tenant_limits(req.tenant_id, {"allowed_tools": tools})
    return req


def reject_tool_request(request_id: str, *, reviewer: str) -> ToolRequest | None:
    return _update_request(request_id, status=ToolRequestStatus.rejected, reviewer=reviewer)


def get_tenant_override_tools(tenant_id: str) -> list[str]:
    from packages.tenant_admin.overrides import get_tenant_override

    cfg = get_tenant_override(tenant_id)
    tools = cfg.get("allowed_tools")
    return [str(t) for t in tools] if isinstance(tools, list) else []
