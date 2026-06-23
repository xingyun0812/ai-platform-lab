"""Phase L #61 — 反馈飞轮 E2E / run_full_cycle 集成测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest

from eval.feedback_loop_demo import run_live_demo, run_mock_demo
from packages.feedback import init_feedback_store, reset_for_tests as reset_fb_store
from packages.feedback.store import Feedback, FeedbackType
from packages.feedback_loop.pipeline import FeedbackLoop, init_feedback_loop, reset_for_tests


def _run(coro):
    return asyncio.run(coro)


def _fb(fid: str, tenant: str = "admin") -> Feedback:
    return Feedback(
        feedback_id=fid,
        tenant_id=tenant,
        session_id="s1",
        message_id=f"m-{fid}",
        feedback_type=FeedbackType.THUMBS_DOWN.value,
        comment="wrong",
        created_at=time.time(),
    )


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_fb_store()
    reset_for_tests()
    yield
    reset_fb_store()
    reset_for_tests()


def test_mock_demo_full_cycle():
    result = _run(run_mock_demo(tenant_id="admin", prompt_id="rag_query"))
    assert result["bad_cases_collected"] == 3
    assert result["ingested_count"] == 3
    assert result["suggestion_id"]
    assert result["bad_cases_file_lines"] == 3
    assert result["error"] is None


def test_full_cycle_empty_store():
    init_feedback_store()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = FeedbackLoop(bad_cases_path=path)
        result = _run(loop.run_full_cycle("admin", "rag_query"))
        assert result["bad_cases_collected"] == 0
        assert result["ingested_count"] == 0
        assert result["suggestion_id"] is None


def test_full_cycle_with_auto_experiment():
    reset_fb_store()
    store = init_feedback_store()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = FeedbackLoop(bad_cases_path=path, auto_experiment=True)
        _run(store.create(_fb("fb-exp-1")))
        result = _run(loop.run_full_cycle("admin", "rag_query"))
        assert result["suggestion_id"]
        assert result["experiment_id"] is not None or result.get("error") is None


def test_collect_respects_since_filter():
    store = init_feedback_store()
    old = _fb("old")
    old.created_at = time.time() - 3600
    new = _fb("new")
    _run(store.create(old))
    _run(store.create(new))
    loop = FeedbackLoop()
    since = time.time() - 60
    cases = _run(loop.collect_bad_cases("admin", since=since))
    ids = {c.feedback_id for c in cases}
    assert "new" in ids
    assert "old" not in ids


def test_ingest_jsonl_schema():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = FeedbackLoop(bad_cases_path=path)
        _run(loop.ingest_to_eval([_fb("x1"), _fb("x2")]))
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        for key in ("feedback_id", "tenant_id", "feedback_type", "message_id"):
            assert key in row


def test_apply_then_manual_experiment():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = FeedbackLoop(bad_cases_path=path)
        sug = _run(loop.generate_prompt_suggestion("rag_query", [_fb("a")]))
        assert loop.apply_suggestion(sug.suggestion_id)
        exp_id = _run(loop.auto_create_experiment(sug))
        assert exp_id is None or exp_id.startswith("exp-")


def test_reject_blocks_experiment():
    loop = FeedbackLoop()
    sug = _run(loop.generate_prompt_suggestion("rag_query", [_fb("r1")]))
    loop.reject_suggestion(sug.suggestion_id)
    exp_id = _run(loop.auto_create_experiment(sug))
    assert exp_id is None


def test_collect_returns_empty_when_store_uninitialized():
    loop = FeedbackLoop()
    cases = _run(loop.collect_bad_cases("admin"))
    assert cases == []


def test_full_cycle_only_negative_feedback():
    store = init_feedback_store()
    _run(store.create(_fb("neg")))
    _run(
        store.create(
            Feedback(
                feedback_id="pos",
                tenant_id="admin",
                session_id="s",
                message_id="m-pos",
                feedback_type=FeedbackType.THUMBS_UP.value,
                created_at=time.time(),
            )
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = FeedbackLoop(bad_cases_path=path)
        result = _run(loop.run_full_cycle("admin", "rag_query"))
        assert result["bad_cases_collected"] == 1


def test_init_feedback_loop_singleton_wiring():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bc.jsonl"
        loop = init_feedback_loop(bad_cases_path=path)
        from packages.feedback_loop.pipeline import get_feedback_loop

        assert get_feedback_loop() is loop


def test_live_demo_skips_when_gateway_down():
    report = _run(run_live_demo(base_url="http://127.0.0.1:1", timeout=1.0))
    assert report["passed"] is False
    assert report.get("error") or report["steps"]
