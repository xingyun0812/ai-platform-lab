from __future__ import annotations

import time
from dataclasses import dataclass

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.bm25_index import Bm25Index, load_index


@dataclass(frozen=True)
class HybridRetrieveTimings:
    vector_ms: float
    bm25_ms: float
    fusion_ms: float

    @property
    def total_ms(self) -> float:
        return self.vector_ms + self.bm25_ms + self.fusion_ms


def rrf_fusion(
    *,
    vector_chunks: list[RetrievedChunk],
    bm25_hits: list[tuple[RetrievedChunk, float]],
    top_k: int,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion：合并向量与 BM25 排名。"""
    t0 = time.perf_counter()
    scores: dict[str, float] = {}
    meta: dict[str, RetrievedChunk] = {}

    for rank, chunk in enumerate(vector_chunks, start=1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
        meta[chunk.chunk_id] = chunk

    for rank, (doc, _bm25_score) in enumerate(bm25_hits, start=1):
        chunk = doc
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)
        if chunk.chunk_id not in meta:
            meta[chunk.chunk_id] = chunk

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    fused: list[RetrievedChunk] = []
    for chunk_id, fused_score in ranked:
        base = meta[chunk_id]
        fused.append(RetrievedChunk(**{**base.model_dump(), "score": fused_score}))
    _ = (time.perf_counter() - t0) * 1000
    return fused


def bm25_to_chunks(hits: list[tuple], kb_id: str) -> list[tuple[RetrievedChunk, float]]:
    out: list[tuple[RetrievedChunk, float]] = []
    for doc, score in hits:
        chunk = RetrievedChunk(
            chunk_id=doc.chunk_id,
            kb_id=doc.kb_id or kb_id,
            version=doc.version,
            source_uri=doc.source_uri,
            offset=doc.offset,
            text=doc.text,
            score=float(score),
        )
        out.append((chunk, float(score)))
    return out


def retrieve_bm25(
    *,
    kb_id: str,
    version: int,
    query: str,
    top_k: int,
) -> tuple[list[tuple[RetrievedChunk, float]], float]:
    t0 = time.perf_counter()
    index: Bm25Index | None = load_index(kb_id, version)
    if index is None:
        return [], (time.perf_counter() - t0) * 1000
    hits = index.search(query, top_k=top_k)
    chunks = bm25_to_chunks(hits, kb_id)
    return chunks, (time.perf_counter() - t0) * 1000
