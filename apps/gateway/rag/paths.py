"""Gateway 薄 re-export — 实现位于 packages.rag.paths（Issue #152）。"""

from packages.rag.paths import resolve_source_path

__all__ = ["resolve_source_path"]
