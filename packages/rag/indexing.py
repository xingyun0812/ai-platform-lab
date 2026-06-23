from __future__ import annotations

import hashlib
from dataclasses import dataclass

from packages.rag.chunker import TextChunk


def content_hash(text: str) -> str:
    """chunk 内容指纹（sha256 前 16 位）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class IncrementalIndexPlan:
    chunks_to_embed: list[TextChunk]
    new_chunks: int
    updated_chunks: int
    skipped_chunks: int
    point_ids_to_delete: list[str]


def plan_incremental_index(
    new_chunks: list[TextChunk],
    existing_rows: list[dict],
) -> IncrementalIndexPlan:
    """
    对比已有向量 payload，决定哪些 chunk 需要重新 embed。

    existing_rows 元素需含: offset, content_hash, point_id
    """
    existing_by_offset: dict[int, dict] = {}
    for row in existing_rows:
        offset = row.get("offset")
        if isinstance(offset, int):
            existing_by_offset[offset] = row

    new_offsets = {c.offset for c in new_chunks}
    point_ids_to_delete = [
        str(row["point_id"])
        for row in existing_rows
        if isinstance(row.get("offset"), int) and row["offset"] not in new_offsets and row.get("point_id")
    ]

    to_embed: list[TextChunk] = []
    new_n = updated_n = skipped_n = 0

    for chunk in new_chunks:
        fp = content_hash(chunk.text)
        old = existing_by_offset.get(chunk.offset)
        if old and old.get("content_hash") == fp:
            skipped_n += 1
            continue
        to_embed.append(chunk)
        if old:
            updated_n += 1
            pid = old.get("point_id")
            if pid:
                point_ids_to_delete.append(str(pid))
        else:
            new_n += 1

    # 去重 delete ids
    delete_ids = list(dict.fromkeys(point_ids_to_delete))

    return IncrementalIndexPlan(
        chunks_to_embed=to_embed,
        new_chunks=new_n,
        updated_chunks=updated_n,
        skipped_chunks=skipped_n,
        point_ids_to_delete=delete_ids,
    )
