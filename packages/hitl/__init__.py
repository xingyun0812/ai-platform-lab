"""packages/hitl — HITL 审批工作流公共接口。

导出：
    ApprovalStatus, ApprovalRequest, ApprovalDecision,
    ApprovalStore, WebhookConfig,
    init_approval_store, get_approval_store, reset_approval_store_for_tests,
    get_approval  (兼容 packages.agent.hitl)
"""
from __future__ import annotations

from packages.hitl.store import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    WebhookConfig,
    get_approval_store,
    init_approval_store,
    reset_approval_store_for_tests,
)


def get_approval(approval_id: str):  # type: ignore[return]
    """向后兼容 packages.agent.hitl.get_approval 的同步包装器。

    注意：这是同步函数，仅用于兼容旧调用方。
    新代码请使用 packages.hitl.service.check_approval (async)。
    """
    import asyncio  # noqa: PLC0415

    store = get_approval_store()
    if store is None:
        return None
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有事件循环中：返回 None，避免死锁
            return None
        return loop.run_until_complete(store.get(approval_id))
    except RuntimeError:
        return asyncio.run(store.get(approval_id))


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalStatus",
    "ApprovalStore",
    "WebhookConfig",
    "get_approval",
    "get_approval_store",
    "init_approval_store",
    "reset_approval_store_for_tests",
]
