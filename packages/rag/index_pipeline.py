"""RAG 索引执行 pipeline — run_index_task（Issue #152 PR-1）。"""

from __future__ import annotations

import logging

from packages.contracts.rag_schemas import TaskStatus
from packages.platform import get_settings, resolve_source_path
from packages.rag.chunker import chunk_text
from packages.rag.embeddings import embed_rag_chunks
from packages.rag.index_metrics import get_index_metrics
from packages.rag.indexing import plan_incremental_index
from packages.rag.source_index import refresh_bm25_after_source_index
from packages.rag.task_store import get_task_store
from packages.rag.vector_store import VectorStore

logger = logging.getLogger("ai_platform.rag.index_pipeline")


async def run_index_task(task_id: str) -> None:
    task_store = get_task_store()
    record = task_store.get(task_id)
    if not record:
        return

    task_store.update(task_id, status=TaskStatus.running)
    settings = get_settings()

    try:
        path = resolve_source_path(record.source_uri)
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在: {record.source_uri}")

        raw = path.read_bytes()

        from packages.rag.multimodal_index import chunk_image_file, is_image_source

        if is_image_source(path):
            chunks = chunk_image_file(
                path,
                source_uri=record.source_uri,
                kb_id=record.kb_id,
                version=record.version,
                raw=raw,
            )
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise ValueError(f"非 UTF-8 文本，无法索引: {record.source_uri}") from e

            if not text.strip():
                raise ValueError(f"文件为空: {record.source_uri}")

            chunks = chunk_text(
                text,
                source_uri=record.source_uri,
                kb_id=record.kb_id,
                version=record.version,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
            )
        if not chunks:
            raise ValueError(f"切分后无有效 chunk: {record.source_uri}")

        store = VectorStore()
        existing = store.list_source_chunks(
            kb_id=record.kb_id,
            version=record.version,
            source_uri=record.source_uri,
        )
        plan = plan_incremental_index(chunks, existing)
        if plan.point_ids_to_delete:
            store.delete_points(plan.point_ids_to_delete)

        to_embed = plan.chunks_to_embed
        if to_embed:
            all_vectors = await embed_rag_chunks(to_embed)
            store.upsert_chunks(
                kb_id=record.kb_id,
                version=record.version,
                chunks=to_embed,
                vectors=all_vectors,
            )

        count = plan.new_chunks + plan.updated_chunks + plan.skipped_chunks
        refresh_bm25_after_source_index(
            store,
            kb_id=record.kb_id,
            version=record.version,
            source_uri=record.source_uri,
            chunks=chunks,
        )
        get_index_metrics().record_index_success(
            kb_id=record.kb_id,
            version=record.version,
            new_chunks=plan.new_chunks,
            updated_chunks=plan.updated_chunks,
            skipped_chunks=plan.skipped_chunks,
        )
        task_store.update(
            task_id,
            status=TaskStatus.success,
            chunks_indexed=count,
            new_chunks=plan.new_chunks,
            updated_chunks=plan.updated_chunks,
            skipped_chunks=plan.skipped_chunks,
            error=None,
        )
        logger.info(
            "index success task_id=%s kb_id=%s version=%s total=%s new=%s updated=%s skipped=%s",
            task_id,
            record.kb_id,
            record.version,
            count,
            plan.new_chunks,
            plan.updated_chunks,
            plan.skipped_chunks,
        )
    except Exception as e:
        logger.exception("index failed task_id=%s", task_id)
        task_store.update(task_id, status=TaskStatus.failed, error=str(e))
