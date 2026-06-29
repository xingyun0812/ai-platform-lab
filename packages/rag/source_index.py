from __future__ import annotations

import logging
from pathlib import Path

from packages.rag.bm25_index import (
    build_index_from_chunks,
    index_path,
    load_index,
    merge_source_into_index,
    remove_source_from_index,
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


def purge_source_index(
    *,
    kb_id: str,
    version: int,
    source_uri: str,
    delete_file: bool = False,
) -> dict[str, int | bool]:
    """从 Qdrant + BM25 移除指定 source；可选删除磁盘文件。"""
    store = VectorStore()
    deleted_vectors = store.delete_source(kb_id=kb_id, version=version, source_uri=source_uri)

    existing = load_index(kb_id, version)
    bm25_docs = 0
    if existing is not None:
        updated = remove_source_from_index(existing, source_uri=source_uri)
        if updated is not None and updated.documents:
            save_index(updated, kb_id, version)
            bm25_docs = len(updated.documents)
        else:
            path = index_path(kb_id, version)
            if path.is_file():
                path.unlink()
            bm25_docs = 0

    file_deleted = False
    if delete_file:
        from packages.platform import resolve_source_path

        try:
            path: Path = resolve_source_path(source_uri)
            if path.is_file():
                path.unlink()
                file_deleted = True
        except ValueError:
            logger.warning("purge skip file delete invalid source_uri=%s", source_uri)

    return {
        "deleted_vectors": deleted_vectors,
        "bm25_docs_remaining": bm25_docs,
        "file_deleted": file_deleted,
    }
