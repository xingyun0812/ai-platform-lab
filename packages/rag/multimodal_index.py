"""RAG 多模态源文件检测与 image chunk 构建（Phase P P2）。"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from packages.rag.chunker import TextChunk

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


def is_image_source(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def guess_image_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "image/png"


def load_image_caption(path: Path) -> str:
    """优先 sidecar：`<name>.caption.txt`，其次 `<stem>.txt`，否则用文件名。"""
    sidecar = path.with_name(path.name + ".caption.txt")
    if sidecar.is_file():
        text = sidecar.read_text(encoding="utf-8").strip()
        if text:
            return text
    alt = path.with_suffix(".txt")
    if alt.is_file() and alt != path:
        text = alt.read_text(encoding="utf-8").strip()
        if text:
            return text
    stem = path.stem.replace("-", " ").replace("_", " ")
    return stem or path.name


def image_content_fingerprint(*, caption: str, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return hashlib.sha256(f"{caption}|{digest}".encode("utf-8")).hexdigest()[:16]


def chunk_image_file(
    path: Path,
    *,
    source_uri: str,
    kb_id: str,
    version: int,
    raw: bytes | None = None,
) -> list[TextChunk]:
    """单张图片 → 单 chunk（caption 供 BM25，向量走 image embed）。"""
    data = raw if raw is not None else path.read_bytes()
    if not data:
        raise ValueError(f"图片为空: {source_uri}")
    caption = load_image_caption(path)
    fp = image_content_fingerprint(caption=caption, raw=data)
    chunk_id = f"{kb_id}:{version}:img:0"
    return [
        TextChunk(
            chunk_id=chunk_id,
            text=caption,
            source_uri=source_uri,
            offset=0,
            modality="image",
            content_fingerprint=fp,
        )
    ]
