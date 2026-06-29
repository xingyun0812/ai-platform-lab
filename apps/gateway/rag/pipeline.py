"""Gateway RAG pipeline — 版本解析仍在此；run_index_task 已下沉 packages（Issue #152）。"""

from __future__ import annotations

import logging

from packages.platform import get_settings
from packages.rag.index_pipeline import run_index_task
from packages.rag.routing import parse_kb_routing, pick_query_version
from packages.rag.task_store import get_task_store
from packages.rag.vector_store import VectorStore

logger = logging.getLogger("ai_platform.rag.pipeline")

task_store = get_task_store()

__all__ = [
    "run_index_task",
    "task_store",
    "resolve_retrieve_version",
    "resolve_query_version",
    "_kb_routing_rules",
    "_list_kb_versions",
]


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
