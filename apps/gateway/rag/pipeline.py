"""Gateway 薄 re-export — 索引与版本解析已下沉 packages（Issue #152）。"""

from packages.rag.index_pipeline import run_index_task
from packages.rag.query_version import (
    kb_routing_rules,
    list_kb_versions,
    resolve_query_version,
    resolve_retrieve_version,
)

# 兼容旧私有名
_kb_routing_rules = kb_routing_rules
_list_kb_versions = list_kb_versions

__all__ = [
    "run_index_task",
    "resolve_retrieve_version",
    "resolve_query_version",
    "kb_routing_rules",
    "list_kb_versions",
    "_kb_routing_rules",
    "_list_kb_versions",
]
