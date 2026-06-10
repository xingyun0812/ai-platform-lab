from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from apps.gateway.settings import REPO_ROOT

APPROVALS_PATH = REPO_ROOT / "data" / "agent_approvals.json"


class ApprovalStatus(StrEnum):
    pending = "pending"
    confirmed = "confirmed"
    rejected = "rejected"


@dataclass
class ExecutionApproval:
    approval_id: str
    tenant_id: str
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    status: ApprovalStatus
    created_at: str
    updated_at: str
    reviewer: str | None = None


def _read_rows() -> list[dict[str, Any]]:
    if not APPROVALS_PATH.is_file():
        return []
    data = json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _write_rows(rows: list[dict[str, Any]]) -> None:
    APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def create_pending_execution(
    *,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> ExecutionApproval:
    now = datetime.now(UTC).isoformat()
    approval = ExecutionApproval(
        approval_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        session_id=session_id,
        tool_name=tool_name,
        arguments=arguments,
        status=ApprovalStatus.pending,
        created_at=now,
        updated_at=now,
    )
    rows = _read_rows()
    rows.append(
        {
            **asdict(approval),
            "status": approval.status.value,
            "arguments": approval.arguments,
        }
    )
    _write_rows(rows)
    return approval


def get_approval(approval_id: str) -> ExecutionApproval | None:
    for row in _read_rows():
        if row.get("approval_id") == approval_id:
            return ExecutionApproval(
                approval_id=str(row["approval_id"]),
                tenant_id=str(row["tenant_id"]),
                session_id=str(row["session_id"]),
                tool_name=str(row["tool_name"]),
                arguments=row.get("arguments") if isinstance(row.get("arguments"), dict) else {},
                status=ApprovalStatus(str(row.get("status", "pending"))),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
                reviewer=row.get("reviewer"),
            )
    return None


def list_pending(*, tenant_id: str | None = None, limit: int = 50) -> list[ExecutionApproval]:
    out: list[ExecutionApproval] = []
    for row in reversed(_read_rows()):
        if row.get("status") != ApprovalStatus.pending:
            continue
        if tenant_id and row.get("tenant_id") != tenant_id:
            continue
        item = get_approval(str(row.get("approval_id")))
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def confirm_execution(*, approval_id: str, reviewer: str) -> ExecutionApproval:
    rows = _read_rows()
    found: dict[str, Any] | None = None
    for row in rows:
        if row.get("approval_id") == approval_id:
            found = row
            break
    if not found:
        raise ValueError(f"approval 不存在: {approval_id}")
    if found.get("status") != ApprovalStatus.pending:
        raise ValueError(f"approval 状态不可确认: {found.get('status')}")
    now = datetime.now(UTC).isoformat()
    found["status"] = ApprovalStatus.confirmed
    found["updated_at"] = now
    found["reviewer"] = reviewer
    _write_rows(rows)
    return get_approval(approval_id)  # type: ignore[return-value]


def reject_execution(*, approval_id: str, reviewer: str) -> ExecutionApproval:
    rows = _read_rows()
    for row in rows:
        if row.get("approval_id") == approval_id:
            if row.get("status") != ApprovalStatus.pending:
                raise ValueError(f"approval 状态不可拒绝: {row.get('status')}")
            row["status"] = ApprovalStatus.rejected
            row["updated_at"] = datetime.now(UTC).isoformat()
            row["reviewer"] = reviewer
            _write_rows(rows)
            return get_approval(approval_id)  # type: ignore[return-value]
    raise ValueError(f"approval 不存在: {approval_id}")
