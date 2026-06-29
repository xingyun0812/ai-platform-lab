#!/usr/bin/env python3
"""tests/test_index_pipeline.py — Issue #152 index pipeline 边界测试。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.contracts.rag_schemas import TaskStatus
from packages.platform import configure, reset_platform_for_tests
from packages.platform.testing import InMemoryPlatformPort, InMemoryPlatformSettings
from packages.rag.index_pipeline import run_index_task
from packages.rag.task_store import get_task_store, reset_task_store_for_tests


def _run(coro):
    return asyncio.run(coro)


class TestRunIndexTask(unittest.TestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()
        reset_task_store_for_tests()
        self.tmp = REPO_ROOT / "tmp_index_pipeline_test"
        self.tmp.mkdir(parents=True, exist_ok=True)
        port = InMemoryPlatformPort(
            settings=InMemoryPlatformSettings(rag_data_root=self.tmp),
        )
        configure(port)

    def tearDown(self) -> None:
        reset_platform_for_tests()
        reset_task_store_for_tests()

    def test_missing_task_is_noop(self) -> None:
        _run(run_index_task("nonexistent-id"))

    @patch("packages.rag.index_pipeline.embed_rag_chunks", new_callable=AsyncMock)
    @patch("packages.rag.index_pipeline.VectorStore")
    def test_success_updates_task(self, mock_store_cls, mock_embed) -> None:
        source = self.tmp / "hello.txt"
        source.write_text("hello index pipeline test content", encoding="utf-8")

        store = mock_store_cls.return_value
        store.list_source_chunks.return_value = []

        from packages.rag.chunker import chunk_text

        chunks = chunk_text(
            source.read_text(encoding="utf-8"),
            source_uri="hello.txt",
            kb_id="kb1",
            version=1,
            chunk_size=512,
            overlap=64,
        )
        mock_embed.return_value = [[0.1] * 8 for _ in chunks]

        record = get_task_store().create(kb_id="kb1", version=1, source_uri="hello.txt")
        _run(run_index_task(record.task_id))

        updated = get_task_store().get(record.task_id)
        assert updated is not None
        self.assertEqual(updated.status, TaskStatus.success)
        self.assertIsNone(updated.error)
        self.assertGreater(updated.chunks_indexed or 0, 0)

    def test_missing_file_marks_failed(self) -> None:
        record = get_task_store().create(
            kb_id="kb1",
            version=1,
            source_uri="missing.txt",
        )
        _run(run_index_task(record.task_id))
        updated = get_task_store().get(record.task_id)
        assert updated is not None
        self.assertEqual(updated.status, TaskStatus.failed)
        self.assertIn("不存在", updated.error or "")


if __name__ == "__main__":
    unittest.main()
