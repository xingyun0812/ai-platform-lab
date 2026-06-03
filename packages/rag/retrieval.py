from __future__ import annotations

from packages.contracts.rag_schemas import RetrievedChunk
from packages.rag.embeddings import embed_texts
from packages.rag.vector_store import VectorStore


async def retrieve_chunks(
    *,
    kb_id: str,
    version: int | None,
    query: str,
    top_k: int,
    resolve_version,
) -> tuple[int, list[RetrievedChunk]]:
    """向量检索；version 为 None 时由 resolve_version 解析为最新已索引版本。"""
    resolved_version = resolve_version(kb_id, version)
    query_vectors = await embed_texts([query])
    store = VectorStore()
    hits = store.retrieve(
        kb_id=kb_id,
        version=resolved_version,
        query_vector=query_vectors[0],
        top_k=top_k,
    )

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
    return resolved_version, chunks
