from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    text: str
    source_uri: str
    offset: int
    modality: str = "text"
    content_fingerprint: str | None = None


def chunk_text(
    text: str,
    *,
    source_uri: str,
    kb_id: str,
    version: int,
    chunk_size: int,
    overlap: int,
) -> list[TextChunk]:
    """按字符窗口切分；overlap 为相邻块重叠字符数。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 须在 [0, chunk_size) 内")

    normalized = text.replace("\r\n", "\n")
    if not normalized.strip():
        return []

    step = chunk_size - overlap
    chunks: list[TextChunk] = []
    offset = 0
    index = 0
    while offset < len(normalized):
        piece = normalized[offset : offset + chunk_size]
        if not piece.strip():
            offset += step
            continue
        chunk_id = f"{kb_id}:{version}:{index}:{offset}"
        chunks.append(
            TextChunk(
                chunk_id=chunk_id,
                text=piece,
                source_uri=source_uri,
                offset=offset,
            )
        )
        index += 1
        offset += step
    return chunks
