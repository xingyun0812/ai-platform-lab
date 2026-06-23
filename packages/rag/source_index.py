from __future__ import annotations

import logging

from packages.rag.bm25_index import (
    build_index_from_chunks,
    load_index,
    merge_source_into_index,
    save_index,
)
from packages.rag.chunker import TextChunk
from packages.rag.vector_store import VectorStore

logger = logging.getLogger("ai_platform.rag.source_index")


def refresh_bm25_after_source_index(
    store: VectorStore,
    *,
    kb_id: str,
    version: int,
    source_uri: str,
    chunks: list[TextChunk],
) -> None:
    """索引单个 source 后刷新 BM25：优先差量 merge，无索引文件时从 Qdrant 引导一次。"""
    existing = load_index(kb_id, version)
    if existing is None:
        all_chunks = store.list_source_chunks_as_text_chunks(kb_id=kb_id, version=version)
        others = [c for c in all_chunks if c.source_uri != source_uri]
        combined = others + chunks
        if not combined:
            return
        index = build_index_from_chunks(combined, kb_id=kb_id, version=version)
    else:
        index = merge_source_into_index(
            existing,
            chunks,
            kb_id=kb_id,
            version=version,
            source_uri=source_uri,
        )
    save_index(index, kb_id, version)
