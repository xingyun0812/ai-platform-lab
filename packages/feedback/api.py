"""反馈 API 层 — Phase J #48

提供高层函数：record_feedback / get_feedback / list_feedback。
负案例自动流入 eval 管道（通过 FeedbackLoop）。
"""

from __future__ import annotations

import logging
import time
import uuid

from packages.feedback.store import (
    Feedback,
    get_feedback_store,
    is_negative,
)

logger = logging.getLogger("ai_platform.feedback.api")


async def record_feedback(
    *,
    tenant_id: str,
    session_id: str,
    message_id: str,
    feedback_type: str,
    rating: int | None = None,
    comment: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> Feedback:
    """创建一条反馈记录；负面反馈自动尝试入库到 eval 管道。"""
    store = get_feedback_store()
    if store is None:
        from packages.feedback.store import InMemoryFeedbackStore

        store = InMemoryFeedbackStore()

    feedback_id = f"fb-{uuid.uuid4().hex[:12]}"
    fb = Feedback(
        feedback_id=feedback_id,
        tenant_id=tenant_id,
        session_id=session_id,
        message_id=message_id,
        feedback_type=feedback_type,
        rating=rating,
        comment=comment,
        user_id=user_id,
        created_at=time.time(),
        metadata=metadata or {},
    )
    await store.create(fb)

    # 负面反馈触发 bad_case 标记（不阻塞主流程）
    if is_negative(feedback_type):
        try:
            from packages.feedback_loop.pipeline import get_feedback_loop

            loop = get_feedback_loop()
            if loop is not None:
                await loop.ingest_to_eval([fb])
        except Exception as exc:
            logger.debug("feedback_loop ingest skipped: %s", exc)

    return fb


async def get_feedback(feedback_id: str) -> Feedback | None:
    store = get_feedback_store()
    if store is None:
        return None
    return await store.get(feedback_id)


async def list_feedback(
    tenant_id: str,
    feedback_type: str | None = None,
    limit: int = 50,
) -> list[Feedback]:
    store = get_feedback_store()
    if store is None:
        return []
    return await store.list(tenant_id, feedback_type=feedback_type, limit=limit)
