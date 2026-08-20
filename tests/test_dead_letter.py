'''tests/test_dead_letter.py — S5: Dead-letter store tests.'''

from __future__ import annotations

import time

from packages.agent.dead_letter import (
    DeadLetterRecord,
    InMemoryDeadLetterStore,
)


class TestInMemoryDeadLetterStore:
    """InMemoryDeadLetterStore CRUD + edge cases."""

    def setup_method(self) -> None:
        self.store = InMemoryDeadLetterStore()

    def test_add_and_get(self) -> None:
        rid = self.store.add_dead_letter(
            DeadLetterRecord(
                id="dl-1",
                task_id="task-1",
                step_id="step-1",
                tool_name="search",
                error_code="TIMEOUT",
                error_message="request timed out",
            )
        )
        assert rid == "dl-1"
        record = self.store.get_dead_letter("dl-1")
        assert record is not None
        assert record.task_id == "task-1"
        assert record.error_code == "TIMEOUT"

    def test_get_nonexistent(self) -> None:
        assert self.store.get_dead_letter("no-such-id") is None

    def test_list_by_task_id(self) -> None:
        self.store.add_dead_letter(
            DeadLetterRecord(id="a", task_id="t1", error_code="E1")
        )
        self.store.add_dead_letter(
            DeadLetterRecord(id="b", task_id="t1", error_code="E2")
        )
        self.store.add_dead_letter(
            DeadLetterRecord(id="c", task_id="t2", error_code="E3")
        )
        records = self.store.list_dead_letters("t1")
        assert len(records) == 2
        assert all(r.task_id == "t1" for r in records)

    def test_list_no_results(self) -> None:
        assert self.store.list_dead_letters("no-such-task") == []

    def test_full_record_roundtrip(self) -> None:
        record = DeadLetterRecord(
            id="dl-full",
            task_id="task-full",
            step_id="s2",
            tool_name="api_call",
            error_code="FORBIDDEN",
            error_message="permission denied",
            context_json={"url": "https://example.com", "method": "GET"},
            created_at=time.time(),
        )
        self.store.add_dead_letter(record)
        loaded = self.store.get_dead_letter("dl-full")
        assert loaded is not None
        assert loaded.tool_name == "api_call"
        assert loaded.context_json == {"url": "https://example.com", "method": "GET"}

    def test_add_empty_id_persists(self) -> None:
        """Empty id is stored as-is (no auto-generation at store layer)."""
        record = DeadLetterRecord(
            id="",
            task_id="t-auto",
            error_code="ERR",
        )
        rid = self.store.add_dead_letter(record)
        assert rid == ""
