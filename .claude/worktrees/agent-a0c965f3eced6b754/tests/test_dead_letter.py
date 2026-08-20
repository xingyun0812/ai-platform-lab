#!/usr/bin/env python3
"""tests/test_dead_letter.py — S5: Dead-letter store unit tests.

Tests basic CRUD operations with InMemoryDeadLetterStore.
No external dependencies required.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path("/Users/zhangyue/IdeaProjects/ai-platform-lab")
sys.path.insert(0, str(REPO_ROOT))


def _ensure_namespace(name: str) -> types.ModuleType:
    if name not in sys.modules:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return sys.modules[name]


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Bootstrap: register namespace packages so importlib chains work
# ---------------------------------------------------------------------------
_ensure_namespace("packages")
_ensure_namespace("packages.contracts")
_ensure_namespace("packages.agent")
_ensure_namespace("apps")
_ensure_namespace("apps.gateway")

# Load dead_letter module directly
_dead_letter = _load_module(
    "packages.agent.dead_letter",
    str(REPO_ROOT / "packages" / "agent" / "dead_letter.py"),
)

# Import symbols
DeadLetterRecord = _dead_letter.DeadLetterRecord
DeadLetterStore = _dead_letter.DeadLetterStore
InMemoryDeadLetterStore = _dead_letter.InMemoryDeadLetterStore
PostgresDeadLetterStore = _dead_letter.PostgresDeadLetterStore
get_dead_letter_store = _dead_letter.get_dead_letter_store
reset_dead_letter_store_for_tests = _dead_letter.reset_dead_letter_store_for_tests
new_dead_letter_id = _dead_letter.new_dead_letter_id


# ---------------------------------------------------------------------------
# TestInMemoryDeadLetterStore
# ---------------------------------------------------------------------------


class TestInMemoryDeadLetterStore(unittest.TestCase):
    """InMemoryDeadLetterStore 的 CRUD 测试。"""

    def setUp(self) -> None:
        reset_dead_letter_store_for_tests()
        self.store = InMemoryDeadLetterStore()

    def _make_record(
        self,
        task_id: str = "task-1",
        error_code: str = "TIMEOUT",
        error_message: str = "Connection timed out",
    ) -> DeadLetterRecord:
        return DeadLetterRecord(
            id=new_dead_letter_id(),
            task_id=task_id,
            step_id="step-1",
            tool_name="web_search",
            error_code=error_code,
            error_message=error_message,
            context_json={"url": "https://example.com"},
        )

    def test_add_dead_letter_returns_id(self) -> None:
        record = self._make_record()
        result_id = self.store.add_dead_letter(record)
        self.assertEqual(result_id, record.id)

    def test_get_dead_letter_returns_record(self) -> None:
        record = self._make_record()
        self.store.add_dead_letter(record)
        fetched = self.store.get_dead_letter(record.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, record.id)
        self.assertEqual(fetched.task_id, "task-1")
        self.assertEqual(fetched.error_code, "TIMEOUT")
        self.assertEqual(fetched.error_message, "Connection timed out")
        self.assertEqual(fetched.tool_name, "web_search")

    def test_get_dead_letter_returns_none_for_missing(self) -> None:
        result = self.store.get_dead_letter("non-existent-id")
        self.assertIsNone(result)

    def test_list_dead_letters_by_task_id(self) -> None:
        r1 = self._make_record(task_id="task-1", error_code="TIMEOUT")
        r2 = self._make_record(task_id="task-1", error_code="RATE_LIMIT")
        r3 = self._make_record(task_id="task-2", error_code="AUTH_ERROR")
        self.store.add_dead_letter(r1)
        self.store.add_dead_letter(r2)
        self.store.add_dead_letter(r3)

        task1_results = self.store.list_dead_letters("task-1")
        self.assertEqual(len(task1_results), 2)
        result_ids = {r.id for r in task1_results}
        self.assertIn(r1.id, result_ids)
        self.assertIn(r2.id, result_ids)

        task2_results = self.store.list_dead_letters("task-2")
        self.assertEqual(len(task2_results), 1)
        self.assertEqual(task2_results[0].id, r3.id)

    def test_list_dead_letters_returns_empty_for_no_results(self) -> None:
        results = self.store.list_dead_letters("non-existent-task")
        self.assertEqual(results, [])

    def test_dead_letter_record_defaults(self) -> None:
        record = DeadLetterRecord(
            id="test-id",
            task_id="task-1",
            error_code="UNKNOWN",
            error_message="something went wrong",
        )
        self.assertIsNone(record.step_id)
        self.assertIsNone(record.tool_name)
        self.assertIsNone(record.context_json)
        self.assertGreater(record.created_at, 0)

    def test_dead_letter_record_to_dict(self) -> None:
        record = self._make_record()
        d = record.to_dict()
        self.assertEqual(d["id"], record.id)
        self.assertEqual(d["task_id"], "task-1")
        self.assertEqual(d["error_code"], "TIMEOUT")
        self.assertEqual(d["context_json"], {"url": "https://example.com"})

    def test_dead_letter_record_from_dict(self) -> None:
        d = {
            "id": "dl-001",
            "task_id": "task-x",
            "step_id": "step-2",
            "tool_name": "calculator",
            "error_code": "DIV_ZERO",
            "error_message": "division by zero",
            "context_json": {"expr": "1/0"},
            "created_at": 1234567890.0,
        }
        record = DeadLetterRecord.from_dict(d)
        self.assertEqual(record.id, "dl-001")
        self.assertEqual(record.task_id, "task-x")
        self.assertEqual(record.step_id, "step-2")
        self.assertEqual(record.tool_name, "calculator")
        self.assertEqual(record.error_code, "DIV_ZERO")
        self.assertEqual(record.context_json, {"expr": "1/0"})
        self.assertEqual(record.created_at, 1234567890.0)

    def test_add_dead_letter_appends_to_list(self) -> None:
        r1 = self._make_record(task_id="multi-task", error_code="ERR1")
        r2 = self._make_record(task_id="multi-task", error_code="ERR2")
        self.store.add_dead_letter(r1)
        self.store.add_dead_letter(r2)
        results = self.store.list_dead_letters("multi-task")
        self.assertEqual(len(results), 2)


# ---------------------------------------------------------------------------
# TestDeadLetterRecord
# ---------------------------------------------------------------------------


class TestDeadLetterRecord(unittest.TestCase):
    """DeadLetterRecord 数据模型测试。"""

    def test_roundtrip_dict(self) -> None:
        record = DeadLetterRecord(
            id="dl-roundtrip",
            task_id="task-rt",
            step_id="s1",
            tool_name="tool-a",
            error_code="ERR",
            error_message="error msg",
            context_json={"key": "value"},
            created_at=1000.0,
        )
        d = record.to_dict()
        restored = DeadLetterRecord.from_dict(d)
        self.assertEqual(restored.id, record.id)
        self.assertEqual(restored.task_id, record.task_id)
        self.assertEqual(restored.step_id, record.step_id)
        self.assertEqual(restored.tool_name, record.tool_name)
        self.assertEqual(restored.error_code, record.error_code)
        self.assertEqual(restored.error_message, record.error_message)
        self.assertEqual(restored.context_json, record.context_json)
        self.assertEqual(restored.created_at, record.created_at)

    def test_from_dict_handles_missing_optional_fields(self) -> None:
        d = {
            "id": "dl-min",
            "task_id": "task-min",
            "error_code": "ERR",
            "error_message": "msg",
            "created_at": 2000.0,
        }
        record = DeadLetterRecord.from_dict(d)
        self.assertIsNone(record.step_id)
        self.assertIsNone(record.tool_name)
        self.assertIsNone(record.context_json)

    def test_new_dead_letter_id_is_unique(self) -> None:
        ids = {new_dead_letter_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main()