from __future__ import annotations

import logging

from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.rag.task_store import get_task_store
from apps.gateway.settings import get_settings
from packages.contracts.rag_schemas import TaskStatus
from packages.rag.bm25_index import build_index_from_chunks, save_index
from packages.rag.chunker import chunk_text
from packages.rag.embeddings import embed_texts
from packages.rag.indexing import plan_incremental_index
from packages.rag.routing import parse_kb_routing, pick_query_version
from packages.rag.vector_store import VectorStore

logger = logging.getLogger("ai_platform.rag.pipeline")

task_store = get_task_store()


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
        existing = store.list_source_chunks(
            kb_id=record.kb_id,
            version=record.version,
            source_uri=record.source_uri,
        )
        plan = plan_incremental_index(chunks, existing)
        if plan.point_ids_to_delete:
            store.delete_points(plan.point_ids_to_delete)

        batch_size = max(1, settings.embedding_batch_size)
        to_embed = plan.chunks_to_embed
        all_vectors: list[list[float]] = []
        if to_embed:
            texts = [c.text for c in to_embed]
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                vectors = await embed_texts(batch)
                all_vectors.extend(vectors)
            store.upsert_chunks(
                kb_id=record.kb_id,
                version=record.version,
                chunks=to_embed,
                vectors=all_vectors,
            )

        count = plan.new_chunks + plan.updated_chunks + plan.skipped_chunks
        all_chunks = store.list_source_chunks_as_text_chunks(
            kb_id=record.kb_id,
            version=record.version,
        )
        if all_chunks:
            bm25 = build_index_from_chunks(all_chunks)
            save_index(bm25, record.kb_id, record.version)
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


def _list_kb_versions(kb_id: str) -> list[int]:
    return VectorStore().list_versions(kb_id)


def _kb_routing_rules():
    from packages.rag.canary_guard import get_kb_routing_override

    raw = _load_rag_yaml().get("kb_routing")
    rules = parse_kb_routing(raw if isinstance(raw, dict) else None)
    for kb_id, rule in list(rules.items()):
        override = get_kb_routing_override(kb_id)
        if override and "canary_percent" in override:
            from packages.rag.routing import KbRoutingRule

            rules[kb_id] = KbRoutingRule(
                stable_version=rule.stable_version,
                canary_version=rule.canary_version,
                canary_percent=int(override["canary_percent"]),
            )
    return rules


def _load_rag_yaml() -> dict:
    import yaml

    path = get_settings().rag_config_path
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_retrieve_version(kb_id: str, version: int | None) -> int:
    ver, _, _ = resolve_query_version(kb_id, version, tenant_id="", query="")
    return ver


def resolve_query_version(
    kb_id: str,
    version: int | None,
    *,
    tenant_id: str,
    query: str,
) -> tuple[int, str, int]:
    """解析查询版本：显式 version 优先，否则按 kb_routing 金丝雀分桶。"""
    return pick_query_version(
        kb_id,
        version,
        tenant_id=tenant_id,
        query=query,
        rules=_kb_routing_rules(),
        list_versions=_list_kb_versions,
    )
