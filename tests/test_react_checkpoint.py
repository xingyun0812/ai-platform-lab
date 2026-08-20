'''tests/test_react_checkpoint.py — S2: ReAct loop checkpoint store tests.'''

from __future__ import annotations

import time

import pytest

from packages.agent.react_checkpoint import (
    InMemoryReactCheckpointStore,
    ReactCheckpoint,
)


class TestInMemoryReactCheckpointStore:
    """InMemoryReactCheckpointStore CRUD + edge cases."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.store = InMemoryReactCheckpointStore()

    def _make_cp(self, cp_id: str, task_id: str, step: int) -> ReactCheckpoint:
        return ReactCheckpoint(
            checkpoint_id=cp_id,
            task_id=task_id,
            step=step,
            messages=[{"role": "user", "content": f"msg-{step}"}],
            trace=[{"tool": "search", "status": "success"}],
            reasoning_trace=[{"step": step, "thinking": "..."}],
            reflect_remaining=1,
            runtime_truncated_tools=0,
            budget_meta={"budget": 4096},
            resolved_model="gpt-4",
            created_at=time.time(),
        )

    @pytest.mark.asyncio
    async def test_save_and_load_latest(self) -> None:
        cp = self._make_cp("cp-1", "task-1", 1)
        await self.store.save(cp)
        loaded = await self.store.load_latest("task-1")
        assert loaded is not None
        assert loaded.checkpoint_id == "cp-1"
        assert loaded.step == 1

    @pytest.mark.asyncio
    async def test_load_latest_returns_newest(self) -> None:
        await self.store.save(self._make_cp("a", "t1", 1))
        await self.store.save(self._make_cp("b", "t1", 2))
        await self.store.save(self._make_cp("c", "t1", 3))
        loaded = await self.store.load_latest("t1")
        assert loaded is not None
        assert loaded.checkpoint_id == "c"
        assert loaded.step == 3

    @pytest.mark.asyncio
    async def test_load_latest_no_checkpoints(self) -> None:
        assert await self.store.load_latest("no-such-task") is None

    @pytest.mark.asyncio
    async def test_list_checkpoints(self) -> None:
        await self.store.save(self._make_cp("a", "t-list", 1))
        await self.store.save(self._make_cp("b", "t-list", 2))
        cps = await self.store.list_checkpoints("t-list", limit=10)
        assert len(cps) == 2
        assert [c.checkpoint_id for c in cps] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_list_checkpoints_limit(self) -> None:
        for i in range(5):
            await self.store.save(self._make_cp(f"cp-{i}", "t-limit", i))
        cps = await self.store.list_checkpoints("t-limit", limit=3)
        assert len(cps) == 3

    @pytest.mark.asyncio
    async def test_list_checkpoints_no_results(self) -> None:
        assert await self.store.list_checkpoints("no-such-task") == []

    @pytest.mark.asyncio
    async def test_delete_task_checkpoints(self) -> None:
        await self.store.save(self._make_cp("a", "t-del", 1))
        await self.store.save(self._make_cp("b", "t-del", 2))
        await self.store.delete_task_checkpoints("t-del")
        assert await self.store.load_latest("t-del") is None
        assert await self.store.list_checkpoints("t-del") == []

    @pytest.mark.asyncio
    async def test_delete_other_task_unaffected(self) -> None:
        await self.store.save(self._make_cp("a", "keep", 1))
        await self.store.save(self._make_cp("b", "remove", 1))
        await self.store.delete_task_checkpoints("remove")
        assert await self.store.load_latest("keep") is not None

    @pytest.mark.asyncio
    async def test_full_roundtrip(self) -> None:
        cp = ReactCheckpoint(
            checkpoint_id="rt",
            task_id="task-rt",
            step=5,
            messages=[{"role": "assistant", "content": "hello"}],
            trace=[{"tool": "calc", "status": "success", "latency_ms": 42.0}],
            reasoning_trace=[],
            reflect_remaining=2,
            runtime_truncated_tools=1,
            budget_meta={"budget": 8192, "truncated": 3},
            resolved_model="claude-3",
            created_at=time.time(),
        )
        await self.store.save(cp)
        loaded = await self.store.load_latest("task-rt")
        assert loaded is not None
        assert loaded.step == 5
        assert loaded.trace[0]["latency_ms"] == 42.0
        assert loaded.budget_meta["budget"] == 8192
        assert loaded.resolved_model == "claude-3"

    @pytest.mark.asyncio
    async def test_top_level_save_function(self) -> None:
        """Test save_react_checkpoint via the global singleton store."""
        from packages.agent.react_checkpoint import (
            reset_react_checkpoint_store_for_tests,
            save_react_checkpoint,
        )

        reset_react_checkpoint_store_for_tests()
        cp_id = await save_react_checkpoint(
            task_id="task-sf",
            step=3,
            messages=[{"role": "user", "content": "hi"}],
            trace=[{"tool": "search", "status": "success"}],
            reflect_remaining=0,
            resolved_model="gpt-4",
        )
        assert isinstance(cp_id, str) and len(cp_id) > 0

        from packages.agent.react_checkpoint import load_latest_react_checkpoint

        loaded = await load_latest_react_checkpoint("task-sf")
        assert loaded is not None
        assert loaded.step == 3
