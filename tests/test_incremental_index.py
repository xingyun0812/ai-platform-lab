#!/usr/bin/env python3
"""tests/test_incremental_index.py — Phase L #55 增量索引规划。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.rag.chunker import TextChunk
from packages.rag.indexing import content_hash, plan_incremental_index


def _chunk(text: str, offset: int = 0) -> TextChunk:
    return TextChunk(
        chunk_id=f"kb:1:0:{offset}",
        text=text,
        source_uri="samples/hello.txt",
        offset=offset,
    )


class TestContentHash(unittest.TestCase):
    def test_stable_hash(self) -> None:
        self.assertEqual(content_hash("hello"), content_hash("hello"))
        self.assertNotEqual(content_hash("hello"), content_hash("world"))


class TestPlanIncrementalIndex(unittest.TestCase):
    def test_all_new_when_no_existing(self) -> None:
        chunks = [_chunk("alpha", 0), _chunk("beta", 512)]
        plan = plan_incremental_index(chunks, [])
        self.assertEqual(plan.new_chunks, 2)
        self.assertEqual(plan.skipped_chunks, 0)
        self.assertEqual(len(plan.chunks_to_embed), 2)

    def test_skip_unchanged_chunks(self) -> None:
        c = _chunk("same text", 0)
        existing = [
            {
                "offset": 0,
                "content_hash": content_hash("same text"),
                "point_id": "p1",
            }
        ]
        plan = plan_incremental_index([c], existing)
        self.assertEqual(plan.skipped_chunks, 1)
        self.assertEqual(plan.new_chunks, 0)
        self.assertEqual(len(plan.chunks_to_embed), 0)

    def test_update_when_text_changes(self) -> None:
        c = _chunk("new text", 0)
        existing = [
            {
                "offset": 0,
                "content_hash": content_hash("old text"),
                "point_id": "p-old",
            }
        ]
        plan = plan_incremental_index([c], existing)
        self.assertEqual(plan.updated_chunks, 1)
        self.assertIn("p-old", plan.point_ids_to_delete)
        self.assertEqual(len(plan.chunks_to_embed), 1)

    def test_delete_removed_offsets(self) -> None:
        existing = [
            {"offset": 0, "content_hash": content_hash("a"), "point_id": "p0"},
            {"offset": 512, "content_hash": content_hash("b"), "point_id": "p1"},
        ]
        plan = plan_incremental_index([_chunk("a", 0)], existing)
        self.assertIn("p1", plan.point_ids_to_delete)

    def test_mixed_new_skip_update(self) -> None:
        existing = [
            {"offset": 0, "content_hash": content_hash("keep"), "point_id": "p0"},
            {"offset": 512, "content_hash": content_hash("old"), "point_id": "p1"},
        ]
        new_chunks = [_chunk("keep", 0), _chunk("changed", 512), _chunk("brand", 1024)]
        plan = plan_incremental_index(new_chunks, existing)
        self.assertEqual(plan.skipped_chunks, 1)
        self.assertEqual(plan.updated_chunks, 1)
        self.assertEqual(plan.new_chunks, 1)
        self.assertEqual(len(plan.chunks_to_embed), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
