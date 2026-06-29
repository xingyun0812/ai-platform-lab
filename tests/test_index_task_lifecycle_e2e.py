#!/usr/bin/env python3
"""tests/test_index_task_lifecycle_e2e.py — Issue #152 PR-3 索引任务 lifecycle E2E。

enqueue → dequeue（模拟 worker）→ run_index_task → task status 终态。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.contracts.rag_schemas import TaskStatus
from packages.platform import configure, reset_platform_for_tests
from packages.platform.testing import InMemoryPlatformPort, InMemoryPlatformSettings
from packages.rag.index_pipeline import run_index_task
from packages.rag.task_store import get_task_store, reset_task_store_for_tests
from packages.tasks.queue import reset_index_task_queue_for_tests


def _run(coro):
    return asyncio.run(coro)


class InMemoryIndexTaskQueue:
    """单测用内存队列，模拟 Redis BLPOP/RPUSH。"""

    def __init__(self) -> None:
        self._pending: deque[str] = deque()

    def enqueue(self, task_id: str) -> None:
        self._pending.append(task_id)

    def dequeue_blocking(self, timeout_seconds: int = 5) -> str | None:
        if self._pending:
            return self._pending.popleft()
        return None


async def _worker_once(queue: InMemoryIndexTaskQueue) -> str | None:
    """模拟 worker 主循环单次 dequeue + run_index_task。"""
    task_id = queue.dequeue_blocking(timeout_seconds=1)
    if not task_id:
        return None
    await run_index_task(task_id)
    return task_id


class TestIndexTaskLifecycleE2E(unittest.TestCase):
    def setUp(self) -> None:
        reset_platform_for_tests()
        reset_task_store_for_tests()
        reset_index_task_queue_for_tests()
        self.tmp = REPO_ROOT / "tmp_index_lifecycle_e2e"
        self.tmp.mkdir(parents=True, exist_ok=True)
        configure(
            InMemoryPlatformPort(
                settings=InMemoryPlatformSettings(rag_data_root=self.tmp),
            )
        )
        self.queue = InMemoryIndexTaskQueue()

    def tearDown(self) -> None:
        reset_platform_for_tests()
        reset_task_store_for_tests()
        reset_index_task_queue_for_tests()

    @patch("packages.rag.index_pipeline.embed_rag_chunks", new_callable=AsyncMock)
    @patch("packages.rag.index_pipeline.VectorStore")
    def test_enqueue_worker_run_success(self, mock_store_cls, mock_embed) -> None:
        source = self.tmp / "doc.txt"
        source.write_text("lifecycle e2e index content here", encoding="utf-8")

        store = mock_store_cls.return_value
        store.list_source_chunks.return_value = []

        from packages.rag.chunker import chunk_text

        chunks = chunk_text(
            source.read_text(encoding="utf-8"),
            source_uri="doc.txt",
            kb_id="kb-e2e",
            version=1,
            chunk_size=512,
            overlap=64,
        )
        mock_embed.return_value = [[0.2] * 8 for _ in chunks]

        record = get_task_store().create(
            kb_id="kb-e2e",
            version=1,
            source_uri="doc.txt",
        )
        self.assertEqual(record.status, TaskStatus.pending)

        self.queue.enqueue(record.task_id)
        with patch("packages.tasks.queue.get_index_task_queue", return_value=self.queue):
            task_id = _run(_worker_once(self.queue))

        self.assertEqual(task_id, record.task_id)
        final = get_task_store().get(record.task_id)
        assert final is not None
        self.assertEqual(final.status, TaskStatus.success)
        self.assertIsNone(final.error)
        self.assertGreater(final.chunks_indexed or 0, 0)

    def test_enqueue_worker_run_failed(self) -> None:
        record = get_task_store().create(
            kb_id="kb-e2e",
            version=1,
            source_uri="ghost.txt",
        )
        self.queue.enqueue(record.task_id)
        task_id = _run(_worker_once(self.queue))
        self.assertEqual(task_id, record.task_id)
        final = get_task_store().get(record.task_id)
        assert final is not None
        self.assertEqual(final.status, TaskStatus.failed)
        self.assertIn("不存在", final.error or "")


class TestWorkerImports(unittest.TestCase):
    def test_worker_module_has_no_gateway_rag_imports(self) -> None:
        worker_main = REPO_ROOT / "apps" / "worker" / "main.py"
        text = worker_main.read_text(encoding="utf-8")
        self.assertNotIn("apps.gateway.rag", text)


if __name__ == "__main__":
    unittest.main()
