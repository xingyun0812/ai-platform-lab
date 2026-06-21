#!/usr/bin/env python3
"""反馈存储单元测试 — Phase J #48

运行：
    python3 tests/test_feedback.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ──── load modules via importlib to avoid chain imports ────

def _load(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_store_mod = _load("packages/feedback/store.py", "packages.feedback.store")
_api_mod = _load("packages/feedback/api.py", "packages.feedback.api")

FeedbackType = _store_mod.FeedbackType
Feedback = _store_mod.Feedback
InMemoryFeedbackStore = _store_mod.InMemoryFeedbackStore
init_feedback_store = _store_mod.init_feedback_store
get_feedback_store = _store_mod.get_feedback_store
reset_for_tests = _store_mod.reset_for_tests
is_negative = _store_mod.is_negative


def _run_async(coro):
    return asyncio.run(coro)


# ─────────────────────────── Tests ───────────────────────────


def test_feedback_type_enum():
    assert FeedbackType.THUMBS_UP.value == "thumbs_up"
    assert FeedbackType.THUMBS_DOWN.value == "thumbs_down"
    assert FeedbackType.BAD_CASE.value == "bad_case"
    assert FeedbackType.RATING_3.value == "rating_3"
    assert len(FeedbackType) == 8
    print("PASS test_feedback_type_enum")


def test_feedback_dataclass_defaults():
    fb = Feedback(
        feedback_id="fb-001",
        tenant_id="t1",
        session_id="s1",
        message_id="m1",
        feedback_type="thumbs_up",
    )
    assert fb.feedback_id == "fb-001"
    assert fb.rating is None
    assert fb.comment is None
    assert fb.user_id is None
    assert isinstance(fb.created_at, float)
    assert fb.metadata == {}
    print("PASS test_feedback_dataclass_defaults")


def test_is_negative():
    assert is_negative("thumbs_down")
    assert is_negative("bad_case")
    assert is_negative("rating_1")
    assert is_negative("rating_2")
    assert not is_negative("thumbs_up")
    assert not is_negative("rating_3")
    assert not is_negative("rating_4")
    assert not is_negative("rating_5")
    assert not is_negative("unknown_type")
    print("PASS test_is_negative")


def test_inmemory_create_get():
    reset_for_tests()
    store = InMemoryFeedbackStore()
    fb = Feedback(
        feedback_id="fb-1",
        tenant_id="t1",
        session_id="s1",
        message_id="m1",
        feedback_type="thumbs_up",
    )

    async def run():
        fid = await store.create(fb)
        assert fid == "fb-1"
        found = await store.get("fb-1")
        assert found is not None
        assert found.feedback_id == "fb-1"
        assert found.feedback_type == "thumbs_up"
        missing = await store.get("does-not-exist")
        assert missing is None

    _run_async(run())
    print("PASS test_inmemory_create_get")


def test_inmemory_list_by_tenant():
    reset_for_tests()
    store = InMemoryFeedbackStore()

    async def run():
        for i in range(5):
            fb = Feedback(
                feedback_id=f"fb-{i}",
                tenant_id="t1",
                session_id="s1",
                message_id=f"m{i}",
                feedback_type="thumbs_up",
            )
            await store.create(fb)
        # different tenant
        fb_other = Feedback(
            feedback_id="fb-other",
            tenant_id="t2",
            session_id="s1",
            message_id="m0",
            feedback_type="thumbs_down",
        )
        await store.create(fb_other)
        items = await store.list("t1")
        assert len(items) == 5
        items2 = await store.list("t2")
        assert len(items2) == 1

    _run_async(run())
    print("PASS test_inmemory_list_by_tenant")


def test_inmemory_list_by_feedback_type():
    reset_for_tests()
    store = InMemoryFeedbackStore()

    async def run():
        types = ["thumbs_up", "thumbs_down", "thumbs_up", "bad_case", "thumbs_up"]
        for i, ft in enumerate(types):
            await store.create(Feedback(
                feedback_id=f"fb-{i}",
                tenant_id="t1",
                session_id="s1",
                message_id=f"m{i}",
                feedback_type=ft,
            ))
        ups = await store.list("t1", feedback_type="thumbs_up")
        assert len(ups) == 3
        downs = await store.list("t1", feedback_type="thumbs_down")
        assert len(downs) == 1

    _run_async(run())
    print("PASS test_inmemory_list_by_feedback_type")


def test_inmemory_list_bad_cases():
    reset_for_tests()
    store = InMemoryFeedbackStore()

    async def run():
        items = [
            ("fb-1", "thumbs_up"),
            ("fb-2", "thumbs_down"),
            ("fb-3", "bad_case"),
            ("fb-4", "rating_1"),
            ("fb-5", "rating_5"),
        ]
        for fid, ft in items:
            await store.create(Feedback(
                feedback_id=fid,
                tenant_id="t1",
                session_id="s1",
                message_id=fid,
                feedback_type=ft,
            ))
        bad_cases = await store.list_bad_cases("t1")
        ids = {bc.feedback_id for bc in bad_cases}
        assert "fb-2" in ids
        assert "fb-3" in ids
        assert "fb-4" in ids
        assert "fb-1" not in ids
        assert "fb-5" not in ids

    _run_async(run())
    print("PASS test_inmemory_list_bad_cases")


def test_inmemory_count_by_type():
    reset_for_tests()
    store = InMemoryFeedbackStore()

    async def run():
        for ft in ["thumbs_up", "thumbs_up", "thumbs_down", "bad_case"]:
            await store.create(Feedback(
                feedback_id=f"fb-{ft}-{time.time_ns()}",
                tenant_id="t1",
                session_id="s1",
                message_id="m1",
                feedback_type=ft,
            ))
        counts = await store.count_by_type("t1")
        assert counts.get("thumbs_up", 0) == 2
        assert counts.get("thumbs_down", 0) == 1
        assert counts.get("bad_case", 0) == 1

    _run_async(run())
    print("PASS test_inmemory_count_by_type")


def test_inmemory_list_limit():
    reset_for_tests()
    store = InMemoryFeedbackStore()

    async def run():
        for i in range(20):
            await store.create(Feedback(
                feedback_id=f"fb-{i}",
                tenant_id="t1",
                session_id="s1",
                message_id=f"m{i}",
                feedback_type="thumbs_up",
                created_at=float(i),
            ))
        items = await store.list("t1", limit=5)
        assert len(items) == 5
        # sorted newest first
        assert items[0].created_at > items[1].created_at

    _run_async(run())
    print("PASS test_inmemory_list_limit")


def test_singleton_init_get():
    reset_for_tests()
    assert get_feedback_store() is None
    store = init_feedback_store()
    assert store is not None
    assert get_feedback_store() is store
    reset_for_tests()
    assert get_feedback_store() is None
    print("PASS test_singleton_init_get")


def test_api_record_feedback():
    """record_feedback API 创建并返回 Feedback 对象"""
    reset_for_tests()
    init_feedback_store()
    record_feedback = _api_mod.record_feedback

    async def run():
        fb = await record_feedback(
            tenant_id="t1",
            session_id="s1",
            message_id="m1",
            feedback_type="thumbs_up",
            rating=None,
            comment="Great!",
        )
        assert fb.feedback_id.startswith("fb-")
        assert fb.tenant_id == "t1"
        assert fb.feedback_type == "thumbs_up"
        assert fb.comment == "Great!"

    _run_async(run())
    print("PASS test_api_record_feedback")


def test_api_get_feedback():
    """get_feedback API 获取单条"""
    reset_for_tests()
    init_feedback_store()
    record_feedback = _api_mod.record_feedback
    get_feedback = _api_mod.get_feedback

    async def run():
        fb = await record_feedback(
            tenant_id="t1",
            session_id="s1",
            message_id="m2",
            feedback_type="thumbs_down",
        )
        found = await get_feedback(fb.feedback_id)
        assert found is not None
        assert found.feedback_id == fb.feedback_id
        missing = await get_feedback("nonexistent-id")
        assert missing is None

    _run_async(run())
    print("PASS test_api_get_feedback")


def test_api_list_feedback():
    """list_feedback API 列出"""
    reset_for_tests()
    init_feedback_store()
    record_feedback = _api_mod.record_feedback
    list_feedback = _api_mod.list_feedback

    async def run():
        for ft in ["thumbs_up", "thumbs_down", "thumbs_up"]:
            await record_feedback(
                tenant_id="t1",
                session_id="s1",
                message_id="m1",
                feedback_type=ft,
            )
        all_items = await list_feedback("t1")
        assert len(all_items) == 3
        ups = await list_feedback("t1", feedback_type="thumbs_up")
        assert len(ups) == 2

    _run_async(run())
    print("PASS test_api_list_feedback")


# ─────────────────────────── Main ────────────────────────────

if __name__ == "__main__":
    tests = [
        test_feedback_type_enum,
        test_feedback_dataclass_defaults,
        test_is_negative,
        test_inmemory_create_get,
        test_inmemory_list_by_tenant,
        test_inmemory_list_by_feedback_type,
        test_inmemory_list_bad_cases,
        test_inmemory_count_by_type,
        test_inmemory_list_limit,
        test_singleton_init_get,
        test_api_record_feedback,
        test_api_get_feedback,
        test_api_list_feedback,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
