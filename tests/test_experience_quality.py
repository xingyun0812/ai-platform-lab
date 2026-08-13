#!/usr/bin/env python3
"""ExperienceStore quality_filter 单元测试 — Issue #206

运行：
    python3 tests/test_experience_quality.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.experience_store import (  # noqa: E402
    ExperienceRecord,
    InMemoryExperienceStore,
    build_experience_record,
    get_experience_metrics,
    quality_filter,
    reset_experience_metrics_for_tests,
    reset_experience_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402
from packages.memory.config import MemoryGovernanceConfig  # noqa: E402


def _setup():
    reset_experience_metrics_for_tests()
    reset_experience_store_for_tests()


def _make_plan(goal: str = "test goal") -> AgentPlan:
    return AgentPlan(goal=goal, steps=[PlanStep(id="s1", description="do thing", depends_on=[])])


def _experience_record(
    lessons: str = "some useful lessons learned from this task",
    goal: str = "test goal",
) -> ExperienceRecord:
    return build_experience_record(
        tenant_id="t1",
        goal=goal,
        plan=_make_plan(goal),
        outcome="success",
        lessons=lessons,
        embedding=[1.0, 0.0],
    )


# ------------------------------------------------------------------------- #
# Unit tests: quality_filter (standalone, ExperienceRecord context)
# ------------------------------------------------------------------------- #


def test_quality_filter_rejects_empty():
    """quality_filter() rejects empty lessons (< min_content_length)."""
    passed, reason = quality_filter(_experience_record(lessons=""))
    assert not passed, "expected reject for empty lessons"
    assert "too short" in reason
    print("PASS test_quality_filter_rejects_empty")


def test_quality_filter_rejects_short():
    """quality_filter() rejects lessons shorter than min_content_length."""
    passed, reason = quality_filter(_experience_record(lessons="hi"))
    assert not passed, "expected reject for short lessons"
    assert "too short" in reason
    print("PASS test_quality_filter_rejects_short")


def test_quality_filter_rejects_punctuation_only():
    """quality_filter() rejects pure punctuation/whitespace/numbers."""
    passed, reason = quality_filter(_experience_record(lessons="!!! ??? ... 12345  !!"))
    assert not passed, "expected reject for punctuation-only"
    assert "no substance" in reason
    print("PASS test_quality_filter_rejects_punctuation_only")


def test_quality_filter_rejects_numbers_only():
    """quality_filter() rejects pure numbers."""
    passed, reason = quality_filter(_experience_record(lessons="12345678901234567890"))
    assert not passed, "expected reject for numbers-only"
    assert "no substance" in reason
    print("PASS test_quality_filter_rejects_numbers_only")


def test_quality_filter_rejects_echo_input():
    """quality_filter() rejects lessons identical to input_message."""
    passed, reason = quality_filter(
        _experience_record(lessons="what is the capital of france"),
        input_message="what is the capital of france",
    )
    assert not passed, "expected reject for echo input"
    assert "echo guard" in reason
    print("PASS test_quality_filter_rejects_echo_input")


def test_quality_filter_rejects_echo_goal():
    """quality_filter() rejects lessons identical to record.goal."""
    passed, reason = quality_filter(
        _experience_record(
            lessons="a long test goal that matches", goal="a long test goal that matches"
        ),
    )
    assert not passed, "expected reject for echo goal"
    assert "echo guard" in reason
    print("PASS test_quality_filter_rejects_echo_goal")


def test_quality_filter_passes_normal_content():
    """quality_filter() passes normal, substantive lessons."""
    passed, reason = quality_filter(
        _experience_record(lessons="The capital of France is Paris and the agent succeeded."),
    )
    assert passed, f"expected pass for normal content, got: {reason}"
    print("PASS test_quality_filter_passes_normal_content")


def test_quality_filter_passes_chinese_content():
    """quality_filter() passes non-ASCII substantive content."""
    passed, reason = quality_filter(
        _experience_record(lessons="用户偏好：喜欢简洁回答，不喜欢冗长解释。"),
    )
    assert passed, f"expected pass for Chinese content, got: {reason}"
    print("PASS test_quality_filter_passes_chinese_content")


def test_quality_filter_configurable_threshold():
    """quality_filter() respects custom min_content_length config."""
    config = MemoryGovernanceConfig(min_content_length=100)
    passed, reason = quality_filter(
        _experience_record(lessons="short but ok under default"),
        config=config,
    )
    assert not passed, "expected reject with custom threshold"
    assert "too short" in reason

    # Also test with config disabled
    config_disabled = MemoryGovernanceConfig(quality_filter_enabled=False)
    passed2, reason2 = quality_filter(
        _experience_record(lessons=""),
        config=config_disabled,
    )
    assert passed2, "expected pass when quality_filter disabled"
    print("PASS test_quality_filter_configurable_threshold")


def test_quality_filter_rejects_mixed_punctuation_and_numbers():
    """quality_filter() rejects content that is only punct/num/whitespace."""
    passed, reason = quality_filter(
        _experience_record(lessons="!@#$% 12345 ^&*()  " + "x" * 20),
    )
    # This has 'x' chars so it has substance -- should pass
    assert passed, f"expected pass for mixed content, got: {reason}"

    # Now test with only punct/num/whitespace (short, caught by length)
    passed2, reason2 = quality_filter(
        _experience_record(lessons="!@#$% 12345 ^&*()  "),
    )
    assert not passed2
    print("PASS test_quality_filter_rejects_mixed_punctuation_and_numbers")


# ------------------------------------------------------------------------- #
# Integration tests: InMemoryExperienceStore.store() with quality_filter
# ------------------------------------------------------------------------- #


def _setup_store() -> InMemoryExperienceStore:
    _setup()
    config = MemoryGovernanceConfig(min_content_length=20, quality_filter_enabled=True)
    return InMemoryExperienceStore(governance_config=config)


def test_store_add_rejects_short_content():
    """ExperienceStore.store() rejects short content via quality_filter."""
    import asyncio

    store = _setup_store()

    async def run():
        r = _experience_record(lessons="short")
        await store.store(r)
        # Should NOT be stored -- get by original id returns None since record not persisted
        got = await store.get(r.experience_id)
        assert got is None, "short lessons should not be stored"

    asyncio.run(run())
    print("PASS test_store_add_rejects_short_content")


def test_store_add_rejects_echo():
    """ExperienceStore.store() rejects echo content when input_message provided."""
    import asyncio

    store = _setup_store()

    async def run():
        r = _experience_record(lessons="What is the capital of France?")
        await store.store(r, input_message="What is the capital of France?")
        got = await store.get(r.experience_id)
        assert got is None, "echo content should not be stored"

    asyncio.run(run())
    print("PASS test_store_add_rejects_echo")


def test_store_add_passes_normal_content():
    """ExperienceStore.store() stores normal content."""
    import asyncio

    store = _setup_store()

    async def run():
        r = _experience_record(lessons="The capital of France is Paris and the agent succeeded.")
        await store.store(r)
        got = await store.get(r.experience_id)
        assert got is not None, "normal content should be stored"
        assert got.lessons == "The capital of France is Paris and the agent succeeded."

    asyncio.run(run())
    print("PASS test_store_add_passes_normal_content")


def test_store_add_metrics_quality_rejected():
    """ExperienceStore.store() increments quality_rejected metric on rejection."""
    import asyncio

    store = _setup_store()

    async def run():
        r = _experience_record(lessons="")
        await store.store(r)
        prom = get_experience_metrics().prometheus_text()
        assert "experience_quality_rejected_total" in prom
        assert 'tenant_id="t1"' in prom

    asyncio.run(run())
    print("PASS test_store_add_metrics_quality_rejected")


def test_store_add_config_disabled():
    """ExperienceStore.store() stores even low-quality content when quality_filter disabled."""
    import asyncio

    _setup()
    config = MemoryGovernanceConfig(quality_filter_enabled=False)
    store = InMemoryExperienceStore(governance_config=config)

    async def run():
        r = _experience_record(lessons="")
        await store.store(r)
        got = await store.get(r.experience_id)
        # Without quality_filter, empty lessons are stored (still may be dedup'd but embedding None for empty)
        assert got is not None, "content should be stored when quality_filter disabled"

    asyncio.run(run())
    print("PASS test_store_add_config_disabled")


# ------------------------------------------------------------------------- #
# Main
# ------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_quality_filter_rejects_empty,
        test_quality_filter_rejects_short,
        test_quality_filter_rejects_punctuation_only,
        test_quality_filter_rejects_numbers_only,
        test_quality_filter_rejects_echo_input,
        test_quality_filter_rejects_echo_goal,
        test_quality_filter_passes_normal_content,
        test_quality_filter_passes_chinese_content,
        test_quality_filter_configurable_threshold,
        test_quality_filter_rejects_mixed_punctuation_and_numbers,
        test_store_add_rejects_short_content,
        test_store_add_rejects_echo,
        test_store_add_passes_normal_content,
        test_store_add_metrics_quality_rejected,
        test_store_add_config_disabled,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
