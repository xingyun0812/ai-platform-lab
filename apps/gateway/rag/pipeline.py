from __future__ import annotations

import logging

from apps.gateway.rag.paths import resolve_source_path
from apps.gateway.rag.task_store import get_task_store
from apps.gateway.settings import get_settings
from packages.contracts.rag_schemas import TaskStatus
from packages.rag.bm25_index import build_index_from_chunks, save_index
from packages.rag.chunker import chunk_text
from packages.rag.embeddings import embed_texts
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
        bm25 = build_index_from_chunks(chunks)
        save_index(bm25, record.kb_id, record.version)
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


def _list_kb_versions(kb_id: str) -> list[int]:
    return VectorStore().list_versions(kb_id)


def _kb_routing_rules():
    from packages.rag.canary_guard import apply_auto_rollback, get_kb_routing_override

    settings = get_settings()
    apply_auto_rollback(
        kb_id="lab-demo",
        min_pass_rate=settings.canary_auto_rollback_min_pass_rate,
    )
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
