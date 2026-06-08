#!/usr/bin/env python3
"""统计 kb 金丝雀命中率（确定性分桶，无需起服务）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from apps.gateway.rag.pipeline import _kb_routing_rules, _list_kb_versions  # noqa: E402
from packages.rag.routing import pick_query_version  # noqa: E402


def simulate(
    *,
    kb_id: str,
    tenant_id: str,
    samples: int,
    query_prefix: str,
) -> dict:
    rules = _kb_routing_rules()
    rule = rules.get(kb_id)
    canary_hits = 0
    stable_hits = 0
    pinned = 0
    for i in range(samples):
        query = f"{query_prefix}-{i}"
        ver, route, bucket = pick_query_version(
            kb_id,
            None,
            tenant_id=tenant_id,
            query=query,
            rules=rules,
            list_versions=_list_kb_versions,
        )
        if route == "canary":
            canary_hits += 1
        elif route == "stable":
            stable_hits += 1
        else:
            pinned += 1
        _ = ver, bucket

    percent = rule.canary_percent if rule else 0
    rate = round(canary_hits / samples, 4) if samples else 0.0
    return {
        "kb_id": kb_id,
        "tenant_id": tenant_id,
        "samples": samples,
        "configured_canary_percent": percent,
        "canary_hits": canary_hits,
        "stable_hits": stable_hits,
        "canary_hit_rate": rate,
        "expected_approx": percent / 100.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="金丝雀路由命中率模拟")
    parser.add_argument("--kb-id", default="lab-demo")
    parser.add_argument("--tenant-id", default="admin")
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--query-prefix", default="canary-probe")
    args = parser.parse_args()
    report = simulate(
        kb_id=args.kb_id,
        tenant_id=args.tenant_id,
        samples=args.samples,
        query_prefix=args.query_prefix,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
