#!/usr/bin/env python3
"""验证 Redis 配额在多次消费后递减（模拟多实例共享）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from apps.gateway.quota import RedisDailyQuotaTracker

    try:
        tracker = RedisDailyQuotaTracker("redis://127.0.0.1:6379/0")
    except Exception as e:
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        raise SystemExit(1)
    tenant = "probe-tenant"
    quota = 3
    results = [tracker.try_consume(tenant, quota) for _ in range(5)]
    ok = results == [True, True, True, False, False]
    print(
        json.dumps(
            {"ok": ok, "results": results, "quota": quota},
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
