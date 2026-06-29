"""RAG 查询版本解析 — kb_routing 金丝雀分桶（Issue #152 PR-2）。"""

from __future__ import annotations

from packages.platform import get_settings
from packages.rag.canary_guard import get_kb_routing_override
from packages.rag.routing import KbRoutingRule, parse_kb_routing, pick_query_version
from packages.rag.vector_store import VectorStore


def list_kb_versions(kb_id: str) -> list[int]:
    return VectorStore().list_versions(kb_id)


def load_rag_yaml() -> dict:
    import yaml

    path = get_settings().rag_config_path
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def kb_routing_rules() -> dict[str, KbRoutingRule]:
    raw = load_rag_yaml().get("kb_routing")
    rules = parse_kb_routing(raw if isinstance(raw, dict) else None)
    for kb_id, rule in list(rules.items()):
        override = get_kb_routing_override(kb_id)
        if override and "canary_percent" in override:
            rules[kb_id] = KbRoutingRule(
                stable_version=rule.stable_version,
                canary_version=rule.canary_version,
                canary_percent=int(override["canary_percent"]),
            )
    return rules


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
        rules=kb_routing_rules(),
        list_versions=list_kb_versions,
    )
