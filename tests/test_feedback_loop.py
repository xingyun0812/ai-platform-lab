#!/usr/bin/env python3
"""反馈飞轮管道单元测试 — Phase J #48

运行：
    python3 tests/test_feedback_loop.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load store first (needed by feedback_loop)
_store_mod = _load("packages/feedback/store.py", "packages.feedback.store")
_feedback_init_mod = _load("packages/feedback/__init__.py", "packages.feedback")
_pipeline_mod = _load("packages/feedback_loop/pipeline.py", "packages.feedback_loop.pipeline")

Feedback = _store_mod.Feedback
FeedbackType = _store_mod.FeedbackType
InMemoryFeedbackStore = _store_mod.InMemoryFeedbackStore
init_feedback_store = _store_mod.init_feedback_store
reset_feedback_store = _store_mod.reset_for_tests

PromptSuggestion = _pipeline_mod.PromptSuggestion
FeedbackLoop = _pipeline_mod.FeedbackLoop
init_feedback_loop = _pipeline_mod.init_feedback_loop
get_feedback_loop = _pipeline_mod.get_feedback_loop
reset_for_tests = _pipeline_mod.reset_for_tests


def _run_async(coro):
    return asyncio.run(coro)


def _make_feedback(fid: str, tenant_id: str = "t1", ft: str = "thumbs_down") -> Feedback:
    return Feedback(
        feedback_id=fid,
        tenant_id=tenant_id,
        session_id="s1",
        message_id=f"msg-{fid}",
        feedback_type=ft,
        comment="This answer was wrong",
        created_at=time.time(),
    )


# ─────────────────────────── Tests ───────────────────────────


def test_prompt_suggestion_dataclass():
    sug = PromptSuggestion(
        suggestion_id="sug-001",
        prompt_id="p1",
        current_version="2",
        suggested_changes="Add examples",
        reasoning="Users confused by lack of examples",
        expected_impact="5-10% satisfaction improvement",
        bad_case_ids=["fb-1", "fb-2"],
    )
    assert sug.suggestion_id == "sug-001"
    assert sug.status == "pending"
    assert isinstance(sug.created_at, float)
    assert len(sug.bad_case_ids) == 2
    print("PASS test_prompt_suggestion_dataclass")


def test_prompt_suggestion_status_values():
    sug = PromptSuggestion(
        suggestion_id="sug-002",
        prompt_id="p1",
        current_version="1",
        suggested_changes="...",
        reasoning="...",
        expected_impact="...",
        bad_case_ids=[],
        status="applied",
    )
    assert sug.status == "applied"
    sug2 = PromptSuggestion(
        suggestion_id="sug-003",
        prompt_id="p1",
        current_version="1",
        suggested_changes="...",
        reasoning="...",
        expected_impact="...",
        bad_case_ids=[],
        status="rejected",
    )
    assert sug2.status == "rejected"
    print("PASS test_prompt_suggestion_status_values")


def test_feedback_loop_collect_bad_cases():
    """collect_bad_cases 从 FeedbackStore 拉取负面反馈"""
    reset_feedback_store()
    reset_for_tests()
    store = init_feedback_store()
    bad_cases_path = Path(tempfile.mktemp(suffix=".jsonl"))

    async def run():
        # 写入一些反馈
        for i in range(3):
            await store.create(_make_feedback(f"fb-neg-{i}", ft="thumbs_down"))
        await store.create(_make_feedback("fb-pos-1", ft="thumbs_up"))

        loop = FeedbackLoop(bad_cases_path=bad_cases_path)
        bad_cases = await loop.collect_bad_cases("t1")
        assert len(bad_cases) == 3
        for bc in bad_cases:
            assert bc.feedback_type in ("thumbs_down", "bad_case", "rating_1", "rating_2")

    _run_async(run())
    print("PASS test_feedback_loop_collect_bad_cases")


def test_feedback_loop_ingest_to_eval():
    """ingest_to_eval 写入 JSONL 文件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        bad_cases = [_make_feedback(f"fb-{i}") for i in range(5)]

        async def run():
            count = await loop.ingest_to_eval(bad_cases)
            assert count == 5
            assert bad_cases_path.exists()
            lines = bad_cases_path.read_text().strip().splitlines()
            assert len(lines) == 5
            # 验证 JSON 格式
            for line in lines:
                record = json.loads(line)
                assert "feedback_id" in record
                assert "tenant_id" in record
                assert "feedback_type" in record

        _run_async(run())
    print("PASS test_feedback_loop_ingest_to_eval")


def test_feedback_loop_ingest_empty():
    """ingest_to_eval 空列表返回 0"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        async def run():
            count = await loop.ingest_to_eval([])
            assert count == 0
            assert not bad_cases_path.exists()

        _run_async(run())
    print("PASS test_feedback_loop_ingest_empty")


def test_feedback_loop_ingest_appends():
    """ingest_to_eval 追加写入（不覆盖）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        async def run():
            batch1 = [_make_feedback(f"fb-a-{i}") for i in range(3)]
            batch2 = [_make_feedback(f"fb-b-{i}") for i in range(2)]
            await loop.ingest_to_eval(batch1)
            await loop.ingest_to_eval(batch2)
            lines = bad_cases_path.read_text().strip().splitlines()
            assert len(lines) == 5

        _run_async(run())
    print("PASS test_feedback_loop_ingest_appends")


def test_feedback_loop_generate_suggestion():
    """generate_prompt_suggestion 生成 PromptSuggestion（LLM 不可用时返回模板）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        bad_cases = [_make_feedback(f"fb-{i}") for i in range(3)]

        async def run():
            suggestion = await loop.generate_prompt_suggestion("prompt-1", bad_cases)
            assert suggestion.suggestion_id.startswith("sug-")
            assert suggestion.prompt_id == "prompt-1"
            assert suggestion.status == "pending"
            assert len(suggestion.bad_case_ids) == 3
            assert suggestion.suggested_changes
            assert suggestion.reasoning
            assert suggestion.expected_impact

        _run_async(run())
    print("PASS test_feedback_loop_generate_suggestion")


def test_feedback_loop_get_suggestion():
    """get_suggestion 和 apply_suggestion 正常工作"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        bad_cases = [_make_feedback("fb-x")]

        async def run():
            sug = await loop.generate_prompt_suggestion("p1", bad_cases)
            # get
            found = loop.get_suggestion(sug.suggestion_id)
            assert found is not None
            assert found.suggestion_id == sug.suggestion_id
            # apply
            ok = loop.apply_suggestion(sug.suggestion_id)
            assert ok
            assert loop.get_suggestion(sug.suggestion_id).status == "applied"
            # reject non-existent
            assert not loop.apply_suggestion("does-not-exist")

        _run_async(run())
    print("PASS test_feedback_loop_get_suggestion")


def test_feedback_loop_auto_create_experiment_not_applied():
    """suggestion.status != 'applied' → auto_create_experiment 返回 None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path)

        sug = PromptSuggestion(
            suggestion_id="sug-test",
            prompt_id="p1",
            current_version="1",
            suggested_changes="...",
            reasoning="...",
            expected_impact="...",
            bad_case_ids=[],
            status="pending",  # not applied
        )

        async def run():
            exp_id = await loop.auto_create_experiment(sug)
            assert exp_id is None

        _run_async(run())
    print("PASS test_feedback_loop_auto_create_experiment_not_applied")


def test_feedback_loop_run_full_cycle():
    """run_full_cycle 编排完整流程"""
    reset_feedback_store()
    reset_for_tests()
    store = init_feedback_store()

    with tempfile.TemporaryDirectory() as tmpdir:
        bad_cases_path = Path(tmpdir) / "bad_cases.jsonl"
        loop = FeedbackLoop(bad_cases_path=bad_cases_path, auto_experiment=False)
        init_feedback_loop.__func__ if hasattr(init_feedback_loop, "__func__") else None  # noqa

        async def run():
            # 加入一些差评
            for i in range(3):
                await store.create(_make_feedback(f"fb-cycle-{i}"))

            result = await loop.run_full_cycle("t1", "prompt-abc")
            assert result["tenant_id"] == "t1"
            assert result["prompt_id"] == "prompt-abc"
            assert result["bad_cases_collected"] == 3
            assert result["ingested_count"] == 3
            assert result["suggestion_id"] is not None
            assert result["experiment_id"] is None  # auto_experiment=False
            assert result["error"] is None

        _run_async(run())
    print("PASS test_feedback_loop_run_full_cycle")


def test_singleton_init_get():
    reset_for_tests()
    assert get_feedback_loop() is None
    loop = init_feedback_loop()
    assert loop is not None
    assert get_feedback_loop() is loop
    reset_for_tests()
    assert get_feedback_loop() is None
    print("PASS test_singleton_init_get")


# ─────────────────────────── Main ────────────────────────────

if __name__ == "__main__":
    tests = [
        test_prompt_suggestion_dataclass,
        test_prompt_suggestion_status_values,
        test_feedback_loop_collect_bad_cases,
        test_feedback_loop_ingest_to_eval,
        test_feedback_loop_ingest_empty,
        test_feedback_loop_ingest_appends,
        test_feedback_loop_generate_suggestion,
        test_feedback_loop_get_suggestion,
        test_feedback_loop_auto_create_experiment_not_applied,
        test_feedback_loop_run_full_cycle,
        test_singleton_init_get,
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
