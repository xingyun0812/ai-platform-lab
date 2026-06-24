#!/usr/bin/env python3
"""tests/test_rerank_providers.py — Phase L #54 rerank provider 单测。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.rerank import rerank_chunks, rerank_provider_name
from packages.rag.rerank_providers import (
    ApiRerankProvider,
    LocalRerankProvider,
    _parse_rerank_scores,
    get_rerank_provider,
)


def _chunk(text: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text=text,
        score=score,
        source_uri="s.txt",
        kb_id="kb",
        version=1,
        offset=0,
    )


class TestStubRerank(unittest.TestCase):
    def test_empty_chunks(self) -> None:
        out, ms = rerank_chunks("q", [], top_n=5, mode="stub")
        self.assertEqual(out, [])
        self.assertGreaterEqual(ms, 0)

    def test_stub_reorders_by_overlap(self) -> None:
        chunks = [_chunk("alpha beta gamma", 0.4), _chunk("unrelated text", 0.9)]
        out, _ = rerank_chunks("alpha beta", chunks, top_n=2, mode="stub")
        self.assertEqual(out[0].text, "alpha beta gamma")

    def test_stub_provider_name(self) -> None:
        self.assertEqual(rerank_provider_name("stub"), "stub")

    def test_unknown_mode_falls_back_stub(self) -> None:
        self.assertEqual(get_rerank_provider("unknown").name, "stub")


class TestApiRerank(unittest.TestCase):
    def test_parse_cohere_like_results(self) -> None:
        body = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]}
        scores = _parse_rerank_scores(body, 2)
        self.assertEqual(scores[1], 0.9)
        self.assertEqual(scores[0], 0.2)

    def test_parse_flat_scores(self) -> None:
        scores = _parse_rerank_scores({"scores": [0.1, 0.8]}, 2)
        self.assertEqual(scores[1], 0.8)

    @patch("httpx.Client")
    def test_api_provider_http(self, client_cls: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": [{"index": 0, "score": 0.95}]}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        client_cls.return_value = mock_client

        provider = ApiRerankProvider(api_url="http://rerank/v1/rerank", api_key="k")
        chunks = [_chunk("hello world", 0.3)]
        out, ms = provider.rerank("hello", chunks, top_n=1)
        self.assertAlmostEqual(out[0].score, 0.95)
        self.assertGreaterEqual(ms, 0)

    def test_api_without_url_falls_back_stub(self) -> None:
        self.assertEqual(get_rerank_provider("api", {}).name, "stub")

    def test_api_mode_with_url(self) -> None:
        p = get_rerank_provider("api", {"api_url": "http://x", "api_key": "k"})
        self.assertEqual(p.name, "api")


class TestLocalRerank(unittest.TestCase):
    def test_local_delegates_stub(self) -> None:
        provider = LocalRerankProvider()
        chunks = [_chunk("foo bar", 0.5)]
        out, _ = provider.rerank("foo", chunks, top_n=1)
        self.assertEqual(len(out), 1)


class TestRerankIntegration(unittest.TestCase):
    def test_tail_preserved(self) -> None:
        chunks = [_chunk(f"c{i}", 0.5) for i in range(5)]
        out, _ = rerank_chunks("c0", chunks, top_n=2, mode="stub")
        self.assertEqual(len(out), 5)

    def test_top_n_limits_candidates(self) -> None:
        chunks = [_chunk("match query", 0.1), _chunk("other", 0.99)]
        out, _ = rerank_chunks("match", chunks, top_n=1, mode="stub")
        self.assertEqual(out[0].text, "match query")


if __name__ == "__main__":
    unittest.main(verbosity=2)
