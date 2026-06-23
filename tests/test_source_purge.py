#!/usr/bin/env python3
"""tests/test_source_purge.py — Phase M purge-source。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.rag.bm25_index import build_index_from_chunks, index_path, load_index, save_index
from packages.rag.chunker import TextChunk
from packages.rag.source_index import purge_source_index


def _chunk(text: str, source: str) -> TextChunk:
    return TextChunk(chunk_id=f"{source}:0", text=text, source_uri=source, offset=0)


class TestPurgeSourceIndex(unittest.TestCase):
    @patch("packages.rag.source_index.VectorStore")
    def test_purge_removes_bm25_source(self, mock_store_cls: MagicMock) -> None:
        mock_store = mock_store_cls.return_value
        mock_store.delete_source.return_value = 2

        kb, ver = "lab-demo", 1
        idx = build_index_from_chunks(
            [_chunk("keep", "other.txt"), _chunk("drop", "drop.txt")],
            kb_id=kb,
            version=ver,
        )
        save_index(idx, kb, ver)
        self.addCleanup(lambda: index_path(kb, ver).unlink(missing_ok=True))

        result = purge_source_index(kb_id=kb, version=ver, source_uri="drop.txt")
        self.assertEqual(result["deleted_vectors"], 2)
        self.assertEqual(result["bm25_docs_remaining"], 1)
        remaining = load_index(kb, ver)
        assert remaining is not None
        self.assertEqual(len(remaining.documents), 1)
        self.assertEqual(remaining.documents[0].source_uri, "other.txt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
