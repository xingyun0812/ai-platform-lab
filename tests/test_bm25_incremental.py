#!/usr/bin/env python3
"""tests/test_bm25_incremental.py — Phase M BM25 按 source 差量 merge。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.rag.bm25_index import (
    build_index_from_chunks,
    merge_source_into_index,
    remove_source_from_index,
)
from packages.rag.chunker import TextChunk


def _chunk(text: str, source: str, offset: int = 0) -> TextChunk:
    return TextChunk(
        chunk_id=f"{source}:{offset}",
        text=text,
        source_uri=source,
        offset=offset,
    )


class TestBm25Incremental(unittest.TestCase):
    def test_merge_replaces_single_source(self) -> None:
        base = build_index_from_chunks(
            [_chunk("alpha", "a.txt"), _chunk("beta", "b.txt")],
            kb_id="kb",
            version=1,
        )
        merged = merge_source_into_index(
            base,
            [_chunk("alpha v2", "a.txt")],
            kb_id="kb",
            version=1,
            source_uri="a.txt",
        )
        sources = {d.source_uri: d.text for d in merged.documents}
        self.assertEqual(sources["a.txt"], "alpha v2")
        self.assertEqual(sources["b.txt"], "beta")
        self.assertEqual(len(merged.documents), 2)

    def test_merge_on_empty_existing(self) -> None:
        merged = merge_source_into_index(
            None,
            [_chunk("only", "solo.txt")],
            kb_id="kb",
            version=1,
            source_uri="solo.txt",
        )
        self.assertEqual(len(merged.documents), 1)

    def test_remove_source(self) -> None:
        base = build_index_from_chunks(
            [_chunk("a", "a.txt"), _chunk("b", "b.txt")],
            kb_id="kb",
            version=1,
        )
        updated = remove_source_from_index(base, source_uri="a.txt")
        assert updated is not None
        self.assertEqual(len(updated.documents), 1)
        self.assertEqual(updated.documents[0].source_uri, "b.txt")

    def test_remove_last_source_returns_none(self) -> None:
        base = build_index_from_chunks([_chunk("a", "a.txt")], kb_id="kb", version=1)
        self.assertIsNone(remove_source_from_index(base, source_uri="a.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
