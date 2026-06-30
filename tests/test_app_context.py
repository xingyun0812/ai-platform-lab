#!/usr/bin/env python3
"""AppContext 单测 — Issue #178 / 架构 §8"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_build_app_context_not_wired_by_default():
    from apps.gateway.app_context import build_app_context
    from apps.gateway.settings import Settings

    settings = Settings()
    ctx = build_app_context(settings)
    assert ctx.settings is settings
    assert ctx.wired is False
    print("PASS test_build_app_context_not_wired_by_default")


def test_app_context_wire_idempotent():
    from apps.gateway.app_context import AppContext, reset_all_for_tests
    from apps.gateway.settings import Settings
    from packages.platform import configure
    from packages.platform.testing import InMemoryPlatformPort

    reset_all_for_tests()
    configure(InMemoryPlatformPort())
    ctx = AppContext(
        settings=Settings(
            feedback_enabled=False,
            agent_plugins_enabled=False,
            orchestrator_enabled=False,
            multi_agent_enabled=False,
        )
    )
    ctx.wire()
    assert ctx.wired is True
    ctx.wire()
    assert ctx.wired is True
    reset_all_for_tests()
    print("PASS test_app_context_wire_idempotent")


def test_reset_all_clears_feedback_store():
    from apps.gateway.app_context import reset_all_for_tests
    from packages.feedback import get_feedback_store, init_feedback_store

    init_feedback_store()
    assert get_feedback_store() is not None
    reset_all_for_tests()
    assert get_feedback_store() is None
    print("PASS test_reset_all_clears_feedback_store")


def test_registry_naming_distinct():
    from packages.embedding import get_embedding_registry
    from packages.embedding import get_registry as get_emb_alias
    from packages.prompt import get_prompt_registry
    from packages.prompt import get_registry as get_prompt_alias

    assert get_prompt_registry is get_prompt_alias
    assert get_embedding_registry is get_emb_alias
    assert get_prompt_registry is not get_embedding_registry
    print("PASS test_registry_naming_distinct")


def test_feedback_api_requires_init():
    from apps.gateway.app_context import reset_all_for_tests
    from packages.feedback.api import record_feedback

    reset_all_for_tests()

    async def run():
        try:
            await record_feedback(
                tenant_id="t1",
                session_id="s1",
                message_id="m1",
                feedback_type="thumbs_up",
            )
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "未初始化" in str(exc)

    asyncio.run(run())
    print("PASS test_feedback_api_requires_init")


if __name__ == "__main__":
    test_build_app_context_not_wired_by_default()
    test_app_context_wire_idempotent()
    test_reset_all_clears_feedback_store()
    test_registry_naming_distinct()
    test_feedback_api_requires_init()
    print("ALL PASS")
