"""packages/agent/hitl — 向后兼容 shim（Phase H #40）

packages.agent.runner 导入 ApprovalStatus 和 get_approval。
新实现委托给 packages.hitl.service（当 HITL_ENABLED=true 时）；
否则沿用原有 JSON 文件存储逻辑。

IMPORTANT：维持原有接口（ApprovalStatus, ExecutionApproval,
create_pending_execution, get_approval, list_pending,
confirm_execution, reject_execution）完全不变。
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

# -------------------------------------------------------------------------
# ApprovalStatus — 保持向后兼容（旧值 confirmed/rejected/pending）
# 同时映射到新 packages.hitl 的 ApprovalStatus
# -------------------------------------------------------------------------
try:
    from enum import StrEnum  # Python 3.11+

    class ApprovalStatus(StrEnum):  # type: ignore[no-redef]
        pending = "pending"
        confirmed = "confirmed"
        rejected = "rejected"
        # 新增状态（向前兼容）
        approved = "approved"
        timeout = "timeout"
        cancelled = "cancelled"

except ImportError:

    class ApprovalStatus(StrEnum):  # type: ignore[no-redef]
        pending = "pending"
        confirmed = "confirmed"
        rejected = "rejected"
        approved = "approved"
        timeout = "timeout"
        cancelled = "cancelled"


# -------------------------------------------------------------------------
# ExecutionApproval dataclass（原接口，不变）
# -------------------------------------------------------------------------
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


# -------------------------------------------------------------------------
# 功能委托：尝试使用新 packages.hitl，失败则用原 JSON 文件实现
# -------------------------------------------------------------------------
_HITL_ENABLED = os.environ.get("HITL_ENABLED", "true").lower() not in ("false", "0", "no")

try:
    from apps.gateway.settings import REPO_ROOT
    APPROVALS_PATH = REPO_ROOT / "data" / "agent_approvals.json"
except Exception:
    from pathlib import Path
    APPROVALS_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_approvals.json"


def _read_rows() -> list[dict[str, Any]]:
    if not APPROVALS_PATH.is_file():
        return []
    data = json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _write_rows(rows: list[dict[str, Any]]) -> None:
    APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _now_iso() -> str:
    from datetime import datetime  # noqa: PLC0415
    return datetime.utcnow().isoformat() + "Z"


def create_pending_execution(
    *,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> ExecutionApproval:
    now = _now_iso()
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
    """查询审批记录。

    若已启用 packages.hitl 且已初始化，优先从新存储中查询；
    否则从 JSON 文件读取（原有逻辑）。
    """
    if _HITL_ENABLED:
        try:
            from packages.hitl import get_approval as _new_get
            req = _new_get(approval_id)
            if req is not None:
                # 将新 ApprovalRequest 映射回 ExecutionApproval
                status_str = getattr(req, "status", "pending")
                # map approved → confirmed for runner.py compatibility
                if status_str == "approved":
                    status_str = "confirmed"
                try:
                    st = ApprovalStatus(status_str)
                except ValueError:
                    st = ApprovalStatus.pending
                return ExecutionApproval(
                    approval_id=req.request_id,
                    tenant_id=req.tenant_id,
                    session_id=req.session_id,
                    tool_name=req.tool_name,
                    arguments=req.arguments,
                    status=st,
                    created_at=str(req.created_at),
                    updated_at=str(getattr(req, "decided_at", req.created_at) or req.created_at),
                    reviewer=req.decided_by,
                )
        except Exception:
            pass  # 降级到 JSON 文件

    # 原有 JSON 文件实现
    for row in _read_rows():
        if row.get("approval_id") == approval_id:
            return ExecutionApproval(
                approval_id=str(row["approval_id"]),
                tenant_id=str(row["tenant_id"]),
                session_id=str(row["session_id"]),
                tool_name=str(row["tool_name"]),
                arguments=(
                    row.get("arguments")
                    if isinstance(row.get("arguments"), dict)
                    else {}
                ),
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
    now = _now_iso()
    found["status"] = ApprovalStatus.confirmed.value
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
            row["status"] = ApprovalStatus.rejected.value
            row["updated_at"] = _now_iso()
            row["reviewer"] = reviewer
            _write_rows(rows)
            return get_approval(approval_id)  # type: ignore[return-value]
    raise ValueError(f"approval 不存在: {approval_id}")
