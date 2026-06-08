from __future__ import annotations

import time

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.bm25_index import tokenize


def _lexical_overlap(query: str, text: str) -> float:
    q_tokens = set(tokenize(query))
    if not q_tokens:
        return 0.0
    t_tokens = set(tokenize(text))
    return len(q_tokens & t_tokens) / len(q_tokens)


def rerank_chunks(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_n: int,
    mode: str = "stub",
) -> tuple[list[RetrievedChunk], float]:
    """对检索候选重排序；默认 stub 用词面重合，无需 GPU。"""
    t0 = time.perf_counter()
    if not chunks or mode != "stub":
        return list(chunks), (time.perf_counter() - t0) * 1000

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
