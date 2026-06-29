from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from packages.platform import get_settings
from packages.rag.chunker import TextChunk
from packages.rag.indexing import chunk_fingerprint

logger = logging.getLogger("ai_platform.rag.vector_store")


class VectorStore:
    """Qdrant 封装：按 kb_id + version 写入与检索。"""

    def __init__(self) -> None:
        settings = get_settings()
        from packages.region.context import get_request_qdrant_url

        qdrant_url = get_request_qdrant_url() or settings.qdrant_url
        self._client = QdrantClient(url=qdrant_url)
        self._collection = settings.qdrant_collection
        self._vector_size = settings.embedding_dimensions

    def ensure_collection(self) -> None:
        if self._client.collection_exists(self._collection):
            info = self._client.get_collection(self._collection)
            size = info.config.params.vectors.size  # type: ignore[union-attr]
            if size != self._vector_size:
                raise RuntimeError(
                    f"集合 {self._collection} 向量维度 {size} 与配置 {self._vector_size} 不一致"
                )
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=self._vector_size, distance=qm.Distance.COSINE),
        )
        logger.info("created qdrant collection=%s size=%s", self._collection, self._vector_size)

    def delete_kb_version(self, kb_id: str, version: int) -> None:
        self.ensure_collection()
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id)),
                        qm.FieldCondition(key="version", match=qm.MatchValue(value=version)),
                    ]
                )
            ),
        )

    def upsert_chunks(
        self,
        *,
        kb_id: str,
        version: int,
        chunks: list[TextChunk],
        vectors: list[list[float]],
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 长度须一致")
        self.ensure_collection()
        points: list[qm.PointStruct] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id))
            payload: dict[str, Any] = {
                "kb_id": kb_id,
                "version": version,
                "chunk_id": chunk.chunk_id,
                "source_uri": chunk.source_uri,
                "offset": chunk.offset,
                "text": chunk.text,
                "modality": chunk.modality,
                "content_hash": chunk_fingerprint(chunk),
            }
            points.append(
                qm.PointStruct(id=point_id, vector=vector, payload=payload),
            )
        if points:
            self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def delete_points(self, point_ids: list[str]) -> None:
        if not point_ids:
            return
        self.ensure_collection()
        self._client.delete(collection_name=self._collection, points_selector=qm.PointIdsList(points=point_ids))

    def delete_source(self, *, kb_id: str, version: int, source_uri: str) -> int:
        """按 kb+version+source_uri 删除向量点，返回删除前估算数量。"""
        self.ensure_collection()
        existing = self.list_source_chunks(kb_id=kb_id, version=version, source_uri=source_uri)
        if not existing:
            return 0
        self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id)),
                        qm.FieldCondition(key="version", match=qm.MatchValue(value=version)),
                        qm.FieldCondition(key="source_uri", match=qm.MatchValue(value=source_uri)),
                    ]
                )
            ),
        )
        return len(existing)

    def list_source_chunks(self, *, kb_id: str, version: int, source_uri: str) -> list[dict[str, Any]]:
        self.ensure_collection()
        rows: list[dict[str, Any]] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id)),
                        qm.FieldCondition(key="version", match=qm.MatchValue(value=version)),
                        qm.FieldCondition(key="source_uri", match=qm.MatchValue(value=source_uri)),
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                rows.append(
                    {
                        "point_id": str(point.id),
                        "chunk_id": payload.get("chunk_id"),
                        "offset": payload.get("offset"),
                        "content_hash": payload.get("content_hash"),
                        "text": payload.get("text"),
                        "source_uri": payload.get("source_uri", source_uri),
                    }
                )
            if offset is None:
                break
        return rows

    def list_source_chunks_as_text_chunks(self, *, kb_id: str, version: int) -> list[TextChunk]:
        self.ensure_collection()
        chunks: list[TextChunk] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id)),
                        qm.FieldCondition(key="version", match=qm.MatchValue(value=version)),
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                chunks.append(
                    TextChunk(
                        chunk_id=str(payload.get("chunk_id") or point.id),
                        text=text,
                        source_uri=str(payload.get("source_uri") or ""),
                        offset=int(payload.get("offset") or 0),
                    )
                )
            if offset is None:
                break
        return chunks

    def retrieve(
        self,
        *,
        kb_id: str,
        version: int | None,
        query_vector: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        self.ensure_collection()
        must: list[qm.FieldCondition] = [
            qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id)),
        ]
        if version is not None:
            must.append(qm.FieldCondition(key="version", match=qm.MatchValue(value=version)))

        response = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=top_k,
            query_filter=qm.Filter(must=must),
            with_payload=True,
        )
        hits = response.points
        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                {
                    "chunk_id": payload.get("chunk_id"),
                    "kb_id": payload.get("kb_id", kb_id),
                    "version": payload.get("version"),
                    "source_uri": payload.get("source_uri"),
                    "offset": payload.get("offset"),
                    "text": payload.get("text"),
                    "score": float(hit.score) if hit.score is not None else 0.0,
                }
            )
        return results

    def list_versions(self, kb_id: str) -> list[int]:
        """扫描 payload 得到已索引版本（实验规模可接受）。"""
        self.ensure_collection()
        versions: set[int] = set()
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection,
                scroll_filter=qm.Filter(
                    must=[qm.FieldCondition(key="kb_id", match=qm.MatchValue(value=kb_id))]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                v = payload.get("version")
                if isinstance(v, int):
                    versions.add(v)
            if offset is None:
                break
        return sorted(versions)
