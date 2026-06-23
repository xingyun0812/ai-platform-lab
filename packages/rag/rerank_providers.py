from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.bm25_index import tokenize

logger = logging.getLogger("ai_platform.rag.rerank")


def _lexical_overlap(query: str, text: str) -> float:
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(tokenize(text))
    return len(q_tokens & t_tokens) / len(q_tokens)


class RerankProvider(ABC):
    name: str = "base"

    @abstractmethod
    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> tuple[list[RetrievedChunk], float]:
        ...


class StubRerankProvider(RerankProvider):
    name = "stub"

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> tuple[list[RetrievedChunk], float]:
        t0 = time.perf_counter()
        if not chunks:
            return [], (time.perf_counter() - t0) * 1000

        limit = max(1, min(top_n, len(chunks)))
        candidates = chunks[:limit]
        scored: list[tuple[float, RetrievedChunk]] = []
        for chunk in candidates:
            lex = _lexical_overlap(query, chunk.text)
            combined = 0.45 * chunk.score + 0.55 * lex
            scored.append((combined, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        reranked: list[RetrievedChunk] = []
        for rank, (score, chunk) in enumerate(scored, start=1):
            normalized = max(score, 0.35 * _lexical_overlap(query, chunk.text) + 0.02 / rank)
            reranked.append(RetrievedChunk(**{**chunk.model_dump(), "score": normalized}))

        tail = chunks[limit:]
        rerank_ms = (time.perf_counter() - t0) * 1000
        return reranked + tail, rerank_ms


class ApiRerankProvider(RerankProvider):
    """HTTP rerank API — OpenAI-style or Cohere-like `{results: [{index, score}]}` JSON."""

    name = "api"

    def __init__(
        self,
        *,
        api_url: str,
        model: str = "",
        api_key: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> tuple[list[RetrievedChunk], float]:
        t0 = time.perf_counter()
        if not chunks:
            return [], (time.perf_counter() - t0) * 1000

        limit = max(1, min(top_n, len(chunks)))
        candidates = chunks[:limit]
        payload: dict[str, Any] = {
            "query": query,
            "documents": [c.text for c in candidates],
        }
        if self.model:
            payload["model"] = self.model

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.api_url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        scores_by_index = _parse_rerank_scores(body, len(candidates))
        scored_pairs: list[tuple[float, RetrievedChunk]] = []
        for idx, chunk in enumerate(candidates):
            score = scores_by_index.get(idx, chunk.score)
            scored_pairs.append((float(score), chunk))
        scored_pairs.sort(key=lambda item: item[0], reverse=True)

        reranked = [
            RetrievedChunk(**{**chunk.model_dump(), "score": score})
            for score, chunk in scored_pairs
        ]
        tail = chunks[limit:]
        rerank_ms = (time.perf_counter() - t0) * 1000
        return reranked + tail, rerank_ms


class LocalRerankProvider(RerankProvider):
    """Local cross-encoder 占位：当前回退 stub，便于后续接 sentence-transformers。"""

    name = "local"

    def __init__(self) -> None:
        self._stub = StubRerankProvider()

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        *,
        top_n: int,
    ) -> tuple[list[RetrievedChunk], float]:
        logger.info("local rerank not implemented; using stub lexical rerank")
        return self._stub.rerank(query, chunks, top_n=top_n)


def _parse_rerank_scores(body: dict[str, Any], n: int) -> dict[int, float]:
    results = body.get("results") or body.get("data") or []
    scores: dict[int, float] = {}
    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            idx = row.get("index", row.get("document_index"))
            if idx is None:
                continue
            raw = row.get("relevance_score", row.get("score", row.get("relevance")))
            if raw is not None:
                scores[int(idx)] = float(raw)
    if scores:
        return scores
    # flat list of scores
    if isinstance(body.get("scores"), list):
        for i, val in enumerate(body["scores"][:n]):
            scores[i] = float(val)
    return scores


def get_rerank_provider(mode: str, config: dict[str, Any] | None = None) -> RerankProvider:
    cfg = config or {}
    normalized = (mode or "stub").strip().lower()
    if normalized == "api":
        url = (cfg.get("api_url") or "").strip()
        if not url:
            logger.warning("rerank mode=api but RAG_RERANK_API_URL empty; fallback stub")
            return StubRerankProvider()
        return ApiRerankProvider(
            api_url=url,
            model=(cfg.get("model") or "").strip(),
            api_key=(cfg.get("api_key") or "").strip(),
            timeout=float(cfg.get("timeout") or 30.0),
        )
    if normalized == "local":
        return LocalRerankProvider()
    return StubRerankProvider()


def provider_config_from_settings(settings: Any) -> dict[str, Any]:
    return {
        "api_url": getattr(settings, "rag_rerank_api_url", "") or "",
        "model": getattr(settings, "rag_rerank_model", "") or "",
        "api_key": (getattr(settings, "rag_rerank_api_key", "") or getattr(settings, "llm_api_key", "") or ""),
        "timeout": getattr(settings, "upstream_timeout_seconds", 30.0),
    }
