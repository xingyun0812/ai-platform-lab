from __future__ import annotations

from pathlib import Path

from apps.gateway.settings import get_settings


def resolve_source_path(source_uri: str) -> Path:
    """将 source_uri 解析为 RAG_DATA_ROOT 下的绝对路径，防止目录穿越。"""
    settings = get_settings()
    root = settings.rag_data_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    rel = Path(source_uri.strip().lstrip("/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("source_uri 须为相对路径且不能包含 ..")

    full = (root / rel).resolve()
    if not str(full).startswith(str(root)):
        raise ValueError("source_uri 超出 RAG_DATA_ROOT")
    return full
