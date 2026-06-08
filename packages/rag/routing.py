from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KbRoutingRule:
    stable_version: int | None = None
    canary_version: int | None = None
    canary_percent: int = 0


def parse_kb_routing(raw: dict[str, Any] | None) -> dict[str, KbRoutingRule]:
    if not isinstance(raw, dict):
        return {}
    rules: dict[str, KbRoutingRule] = {}
    for kb_id, cfg in raw.items():
        if not isinstance(kb_id, str) or not isinstance(cfg, dict):
            continue
        stable = cfg.get("stable_version")
        canary = cfg.get("canary_version")
        percent = cfg.get("canary_percent", 0)
        rules[kb_id] = KbRoutingRule(
            stable_version=int(stable) if isinstance(stable, int) else None,
            canary_version=int(canary) if isinstance(canary, int) else None,
            canary_percent=max(0, min(100, int(percent))) if isinstance(percent, int) else 0,
        )
    return rules


def routing_bucket(tenant_id: str, query: str) -> int:
    """确定性分桶 0–99，同一 tenant+query 始终同一路由。"""
    digest = hashlib.sha256(f"{tenant_id}:{query}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def pick_query_version(
    kb_id: str,
    explicit_version: int | None,
    *,
    tenant_id: str,
    query: str,
    rules: dict[str, KbRoutingRule],
    list_versions: Callable[[str], list[int]],
) -> tuple[int, str, int]:
    """返回 (version, route_label, bucket)。explicit_version 时忽略金丝雀。"""
    if explicit_version is not None:
        return explicit_version, "pinned", routing_bucket(tenant_id, query)

    versions = list_versions(kb_id)
    if not versions:
        raise ValueError(f"知识库 {kb_id} 尚无已索引版本")

    rule = rules.get(kb_id)
    bucket = routing_bucket(tenant_id, query)
    if rule is None or rule.canary_percent <= 0 or rule.canary_version is None:
        stable = _stable_version(rule, versions)
        return stable, "stable", bucket

    canary = rule.canary_version
    if canary not in versions:
        stable = _stable_version(rule, versions)
        return stable, "stable", bucket

    stable = _stable_version(rule, versions, exclude={canary})
    if bucket < rule.canary_percent:
        return canary, "canary", bucket
    return stable, "stable", bucket


def _stable_version(
    rule: KbRoutingRule | None,
    versions: list[int],
    *,
    exclude: set[int] | None = None,
) -> int:
    skip = exclude or set()
    if rule and rule.stable_version is not None and rule.stable_version in versions:
        if rule.stable_version not in skip:
            return rule.stable_version
    candidates = [v for v in versions if v not in skip]
    return max(candidates) if candidates else max(versions)


def describe_routing(
    kb_id: str,
    *,
    rules: dict[str, KbRoutingRule],
    list_versions: Callable[[str], list[int]],
) -> dict[str, Any]:
    versions = list_versions(kb_id)
    rule = rules.get(kb_id)
    if rule is None:
        latest = max(versions) if versions else None
        return {
            "kb_id": kb_id,
            "indexed_versions": versions,
            "stable_version": latest,
            "canary_version": None,
            "canary_percent": 0,
            "rollback_hint": "未配置 kb_routing；省略 version 时使用 latest",
        }

    canary = rule.canary_version
    stable = _stable_version(rule, versions, exclude={canary} if canary else None)
    return {
        "kb_id": kb_id,
        "indexed_versions": versions,
        "stable_version": stable,
        "canary_version": canary if canary in versions else canary,
        "canary_percent": rule.canary_percent,
        "rollback_hint": "将 canary_percent 调为 0 即可全量回滚到 stable_version",
    }
