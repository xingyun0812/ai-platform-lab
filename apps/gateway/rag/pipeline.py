from __future__ import annotations

import logging

from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.rag.task_store import IndexTaskStore
from packages.contracts.rag_schemas import TaskStatus
from apps.gateway.settings import get_settings
from packages.rag.chunker import chunk_text
from packages.rag.embeddings import embed_texts
from packages.rag.vector_store import VectorStore

logger = logging.getLogger("ai_platform.rag.pipeline")

task_store = IndexTaskStore()


async def run_index_task(task_id: str) -> None:
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
        store.delete_kb_version(record.kb_id, record.version)

        batch_size = max(1, settings.embedding_batch_size)
        all_vectors: list[list[float]] = []
        texts = [c.text for c in chunks]
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vectors = await embed_texts(batch)
            all_vectors.extend(vectors)

        count = store.upsert_chunks(
            kb_id=record.kb_id,
            version=record.version,
            chunks=chunks,
            vectors=all_vectors,
        )
        task_store.update(
            task_id,
            status=TaskStatus.success,
            chunks_indexed=count,
            error=None,
        )
        logger.info(
            "index success task_id=%s kb_id=%s version=%s chunks=%s",
            task_id,
            record.kb_id,
            record.version,
            count,
        )
    except Exception as e:
        logger.exception("index failed task_id=%s", task_id)
        task_store.update(task_id, status=TaskStatus.failed, error=str(e))


def resolve_retrieve_version(kb_id: str, version: int | None) -> int:
    if version is not None:
        return version
    store = VectorStore()
    versions = store.list_versions(kb_id)
    if not versions:
        raise ValueError(f"知识库 {kb_id} 尚无已索引版本")
    return max(versions)
