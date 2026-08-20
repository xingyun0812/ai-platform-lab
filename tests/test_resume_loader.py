"""tests/test_resume_loader.py — S4: ReAct resume context loader tests."""

from __future__ import annotations

import time

import pytest

from packages.agent.react_checkpoint import (
    ReactCheckpoint,
    get_react_checkpoint_store,
    reset_react_checkpoint_store_for_tests,
)
from packages.agent.react_resume_loader import (
    load_react_resume_context,
)


class TestLoadReactResumeContext:
    """Tests for load_react_resume_context using InMemoryReactCheckpointStore."""

    @pytest.fixture(autouse=True)
    def _setup_and_teardown(self) -> None:
        reset_react_checkpoint_store_for_tests()
        yield
        reset_react_checkpoint_store_for_tests()

    async def _seed_checkpoint(self) -> str:
        """Save a realistic checkpoint and return the task_id."""
        store = get_react_checkpoint_store()
        cp = ReactCheckpoint(
            checkpoint_id="cp-resume-1",
            task_id="task-resume",
            step=3,
            messages=[
                {"role": "user", "content": "What is the weather?"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\": \"Beijing\"}"}},
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": "25C, sunny"},
                {"role": "assistant", "content": "The weather in Beijing is 25C and sunny."},
            ],
            trace=[
                {"tool_name": "get_weather", "arguments": {"city": "Beijing"}, "status": "success", "result": "25C, sunny", "latency_ms": 150.0, "attempt": 0},
            ],
            reasoning_trace=[
                {"step": 1, "thinking": "User wants weather info", "visible_content": None},
                {"step": 2, "thinking": "Calling get_weather for Beijing", "visible_content": None},
            ],
            reflect_remaining=2,
            runtime_truncated_tools=0,
            budget_meta={"budget": 8192, "estimated_tokens": 450, "truncated_messages": 0, "truncated_tool_results": 0, "summary_applied": False, "keep_recent_turns": 10},
            resolved_model="gpt-4",
            created_at=time.time(),
        )
        await store.save(cp)
        return "task-resume"

    @pytest.mark.asyncio
    async def test_load_resume_context_returns_none_when_no_checkpoint(self) -> None:
        """Should return None when no checkpoint exists."""
        ctx = await load_react_resume_context("no-such-task")
        assert ctx is None

    @pytest.mark.asyncio
    async def test_load_resume_context_basic_fields(self) -> None:
        """Should restore basic scalar fields from checkpoint."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert ctx.resolved_model == "gpt-4"
        assert ctx.reflect_remaining == 2
        assert ctx.runtime_truncated_tools == 0
        assert ctx.resume_step == 4  # step 3 + 1

    @pytest.mark.asyncio
    async def test_load_resume_context_messages(self) -> None:
        """Should restore messages list from checkpoint."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert len(ctx.messages) == 4
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[0]["content"] == "What is the weather?"
        assert ctx.messages[3]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_load_resume_context_session_messages(self) -> None:
        """session_messages should mirror messages."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert ctx.session_messages == ctx.messages

    @pytest.mark.asyncio
    async def test_load_resume_context_trace(self) -> None:
        """Should restore ToolCallRecord trace from checkpoint."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert len(ctx.trace) == 1
        record = ctx.trace[0]
        assert record.tool_name == "get_weather"
        assert record.status == "success"
        assert record.result == "25C, sunny"
        assert record.latency_ms == 150.0

    @pytest.mark.asyncio
    async def test_load_resume_context_reasoning_trace(self) -> None:
        """Should restore ReasoningTraceRecord reasoning_trace from checkpoint."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert len(ctx.reasoning_trace) == 2
        r1 = ctx.reasoning_trace[0]
        assert r1.step == 1
        assert r1.thinking == "User wants weather info"
        assert r1.visible_content is None

    @pytest.mark.asyncio
    async def test_load_resume_context_budget_meta(self) -> None:
        """Should restore budget_meta dict from checkpoint."""
        task_id = await self._seed_checkpoint()
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert ctx.budget_meta["budget"] == 8192
        assert ctx.budget_meta["estimated_tokens"] == 450
        assert ctx.budget_meta["summary_applied"] is False

    @pytest.mark.asyncio
    async def test_load_resume_context_empty_trace(self) -> None:
        """Should handle checkpoint with no trace gracefully."""
        store = get_react_checkpoint_store()
        cp = ReactCheckpoint(
            checkpoint_id="cp-empty",
            task_id="task-empty",
            step=1,
            messages=[{"role": "user", "content": "hello"}],
            trace=[],
            reasoning_trace=[],
            reflect_remaining=0,
            runtime_truncated_tools=0,
            budget_meta={},
            resolved_model="",
            created_at=time.time(),
        )
        await store.save(cp)
        ctx = await load_react_resume_context("task-empty")
        assert ctx is not None
        assert ctx.trace == []
        assert ctx.reasoning_trace == []
        assert ctx.budget_meta == {}
        assert ctx.resolved_model == ""
        assert ctx.resume_step == 2

    @pytest.mark.asyncio
    async def test_load_resume_context_multiple_checkpoints(self) -> None:
        """Should load the latest checkpoint (highest step)."""
        store = get_react_checkpoint_store()
        task_id = "task-multi"
        for step in range(1, 4):
            cp = ReactCheckpoint(
                checkpoint_id=f"cp-{step}",
                task_id=task_id,
                step=step,
                messages=[{"role": "user", "content": f"msg-{step}"}],
                trace=[{"tool_name": "search", "status": "success", "latency_ms": 10.0, "attempt": 0}],
                reasoning_trace=[{"step": step, "thinking": f"step {step}"}],
                reflect_remaining=3 - step,
                runtime_truncated_tools=0,
                budget_meta={"budget": 4096},
                resolved_model="gpt-4",
                created_at=time.time(),
            )
            await store.save(cp)
        ctx = await load_react_resume_context(task_id)
        assert ctx is not None
        assert ctx.resume_step == 4  # step 3 + 1
        assert ctx.reflect_remaining == 0  # step 3 has reflect_remaining=0

    @pytest.mark.asyncio
    async def test_load_resume_context_malformed_trace(self) -> None:
        """Should gracefully handle malformed trace records."""
        store = get_react_checkpoint_store()
        cp = ReactCheckpoint(
            checkpoint_id="cp-mal",
            task_id="task-mal",
            step=1,
            messages=[{"role": "user", "content": "hi"}],
            trace=[{"not_a_tool_record": True}],  # missing required fields
            reasoning_trace=[],
            reflect_remaining=0,
            runtime_truncated_tools=0,
            budget_meta={},
            resolved_model="",
            created_at=time.time(),
        )
        await store.save(cp)
        ctx = await load_react_resume_context("task-mal")
        assert ctx is not None
        assert len(ctx.trace) == 1
        record = ctx.trace[0]
        assert record.tool_name == ""  # graceful fallback
        assert record.status == "completed"  # default fallback

    @pytest.mark.asyncio
    async def test_load_resume_context_malformed_reasoning(self) -> None:
        """Should gracefully handle malformed reasoning trace records."""
        store = get_react_checkpoint_store()
        cp = ReactCheckpoint(
            checkpoint_id="cp-mal-reason",
            task_id="task-mal-reason",
            step=1,
            messages=[{"role": "user", "content": "hi"}],
            trace=[],
            reasoning_trace=[{"not_a_reasoning_record": True, "step": 0}],
            reflect_remaining=0,
            runtime_truncated_tools=0,
            budget_meta={},
            resolved_model="",
            created_at=time.time(),
        )
        await store.save(cp)
        ctx = await load_react_resume_context("task-mal-reason")
        assert ctx is not None
        assert len(ctx.reasoning_trace) == 1
        r = ctx.reasoning_trace[0]
        assert r.step == 0
        assert r.thinking == ""
