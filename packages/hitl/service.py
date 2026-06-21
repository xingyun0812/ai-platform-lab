"""HITL 业务服务层 — Phase H #40

提供审批生命周期管理：创建、查询、审批、拒绝、取消、超时扫描。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from packages.hitl.store import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    WebhookConfig,
    get_approval_store,
)

logger = logging.getLogger("ai_platform.hitl.service")


async def request_approval(
    *,
    tenant_id: str,
    session_id: str,
    tool_name: str,
    arguments: dict,
    timeout_seconds: int = 300,
    webhook: Optional[WebhookConfig] = None,
    metadata: Optional[dict] = None,
) -> ApprovalRequest:
    """创建审批请求，发送 webhook（如配置），返回 ApprovalRequest。"""
    store = get_approval_store()
    if store is None:
        raise RuntimeError("ApprovalStore 未初始化，请先调用 init_approval_store()")

    now = time.time()
    req = ApprovalRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        session_id=session_id,
        tool_name=tool_name,
        arguments=arguments,
        created_at=now,
        expires_at=now + timeout_seconds,
        status="pending",
        metadata=metadata or {},
    )
    await store.create(req)

    if webhook and webhook.enabled:
        try:
            from packages.hitl.webhook import send_webhook  # noqa: PLC0415
            sent = await send_webhook(
                webhook,
                {
                    "event": "hitl.approval_requested",
                    "request_id": req.request_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "expires_at": req.expires_at,
                },
            )
            req.webhook_sent = sent
        except Exception as exc:
            logger.warning("webhook 发送失败: %s", exc)

    return req


async def check_approval(request_id: str) -> ApprovalStatus:
    """查询审批状态。"""
    store = get_approval_store()
    if store is None:
        return ApprovalStatus.PENDING
    req = await store.get(request_id)
    if req is None:
        return ApprovalStatus.PENDING
    status_map = {
        "pending": ApprovalStatus.PENDING,
        "approved": ApprovalStatus.APPROVED,
        "rejected": ApprovalStatus.REJECTED,
        "timeout": ApprovalStatus.TIMEOUT,
        "cancelled": ApprovalStatus.CANCELLED,
    }
    return status_map.get(req.status, ApprovalStatus.PENDING)


async def approve(
    request_id: str,
    decided_by: str,
    reason: Optional[str] = None,
) -> Optional[ApprovalRequest]:
    """批准审批请求。"""
    store = get_approval_store()
    if store is None:
        raise RuntimeError("ApprovalStore 未初始化")
    decision = ApprovalDecision(
        request_id=request_id,
        status="approved",
        decided_by=decided_by,
        reason=reason,
        decided_at=time.time(),
    )
    result = await store.decide(decision)
    if result is None:
        raise ValueError(f"审批不存在或已处理: {request_id}")
    return result


async def reject(
    request_id: str,
    decided_by: str,
    reason: Optional[str] = None,
) -> Optional[ApprovalRequest]:
    """拒绝审批请求。"""
    store = get_approval_store()
    if store is None:
        raise RuntimeError("ApprovalStore 未初始化")
    decision = ApprovalDecision(
        request_id=request_id,
        status="rejected",
        decided_by=decided_by,
        reason=reason,
        decided_at=time.time(),
    )
    result = await store.decide(decision)
    if result is None:
        raise ValueError(f"审批不存在或已处理: {request_id}")
    return result


async def timeout_expired_requests() -> int:
    """扫描并标记超时的审批请求，返回处理数量。"""
    store = get_approval_store()
    if store is None:
        return 0
    return await store.expire_stale()
