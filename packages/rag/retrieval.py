from __future__ import annotations

import time

from packages.contracts.rag_schemas import RetrievedChunk
from packages.platform import get_settings
from packages.rag.embeddings import embed_texts
from packages.rag.hybrid import HybridRetrieveTimings, retrieve_bm25, rrf_fusion
from packages.rag.vector_store import VectorStore


def _hits_to_chunks(hits: list[dict], kb_id: str) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for hit in hits:
        chunk_id = hit.get("chunk_id")
        ver = hit.get("version")
        source_uri = hit.get("source_uri")
        text = hit.get("text")
        if not isinstance(chunk_id, str) or not isinstance(ver, int):
            continue
        if not isinstance(source_uri, str) or not isinstance(text, str):
            continue
        offset = hit.get("offset")
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                kb_id=str(hit.get("kb_id", kb_id)),
                version=ver,
                source_uri=source_uri,
                offset=int(offset) if isinstance(offset, int) else 0,
                text=text,
                score=float(hit.get("score", 0.0)),
            )
        )
    return chunks


async def _retrieve_vector(
    *,
    kb_id: str,
    version: int,
    query: str,
    top_k: int,
) -> tuple[list[RetrievedChunk], float]:
    t0 = time.perf_counter()
    query_vectors = await embed_texts([query])
    store = VectorStore()
    hits = store.retrieve(
        kb_id=kb_id,
        version=version,
        query_vector=query_vectors[0],
        top_k=top_k,
    )
    return _hits_to_chunks(hits, kb_id), (time.perf_counter() - t0) * 1000


async def retrieve_chunks(
    *,
    kb_id: str,
    version: int | None,
    query: str,
    top_k: int,
    resolve_version,
) -> tuple[int, list[RetrievedChunk], HybridRetrieveTimings | None]:
    """检索；hybrid 模式时 RRF 融合向量 + BM25。"""
    resolved_version = resolve_version(kb_id, version)
    settings = get_settings()

    if settings.rag_retrieval_mode != "hybrid":
        vector_chunks, vector_ms = await _retrieve_vector(
            kb_id=kb_id,
            version=resolved_version,
            query=query,
            top_k=top_k,
        )
        return resolved_version, vector_chunks, HybridRetrieveTimings(
            vector_ms=vector_ms,
            bm25_ms=0.0,
            fusion_ms=0.0,
        )

    vector_chunks, vector_ms = await _retrieve_vector(
        kb_id=kb_id,
        version=resolved_version,
        query=query,
        top_k=settings.rag_bm25_top_k,
    )
    bm25_hits, bm25_ms = retrieve_bm25(
        kb_id=kb_id,
        version=resolved_version,
        query=query,
        top_k=settings.rag_bm25_top_k,
    )
    t_fuse = time.perf_counter()
    fused = rrf_fusion(
        vector_chunks=vector_chunks,
        bm25_hits=bm25_hits,
        top_k=top_k,
        rrf_k=settings.rag_hybrid_rrf_k,
    )
    fusion_ms = (time.perf_counter() - t_fuse) * 1000
    return resolved_version, fused, HybridRetrieveTimings(
        vector_ms=vector_ms,
        bm25_ms=bm25_ms,
        fusion_ms=fusion_ms,
    )
