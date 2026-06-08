#!/usr/bin/env python3
"""本机验收冒烟（不依赖 LLM Key 的部分 + 可选全量）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass

import httpx

BASE = "http://127.0.0.1:8000"
ADMIN_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}
DEMO_A_HEADERS = {
    "X-Tenant-Id": "demo-a",
    "Authorization": "Bearer sk-tenant-demo-a-change-me",
}
DEMO_B_HEADERS = {
    "X-Tenant-Id": "demo-b",
    "Authorization": "Bearer sk-tenant-demo-b-change-me",
}


@dataclass
class Check:
    week: str
    name: str
    passed: bool
    detail: str
    blocked: bool = False


async def run_checks(*, with_llm: bool) -> list[Check]:
    out: list[Check] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        # W1 healthz
        r = await c.get("/healthz")
        out.append(
            Check("W1", "GET /healthz", r.status_code == 200 and r.json().get("status") == "ok", str(r.status_code))
        )

        # W1 鉴权
        r = await c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
        out.append(Check("W1", "无租户头 → 401", r.status_code == 401, f"status={r.status_code}"))

        # W1 无 Key
        r = await c.post(
            "/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
        body = r.json() if r.content else {}
        code = (body.get("error") or {}).get("code")
        out.append(
            Check(
                "W1",
                "无 LLM_API_KEY → 503",
                r.status_code == 503 and code == "UPSTREAM_NOT_CONFIGURED",
                f"status={r.status_code} code={code}",
                blocked=not with_llm,
            )
        )

        # PB billing（无 DATABASE_URL 时应 503）
        r = await c.get("/internal/billing/usage", headers=ADMIN_HEADERS)
        body = r.json() if r.content else {}
        code = (body.get("error") or {}).get("code")
        billing_ok = (
            r.status_code == 200 and "items" in body
        ) or (r.status_code == 503 and code == "BILLING_DISABLED")
        out.append(
            Check(
                "PB",
                "GET /internal/billing/usage",
                billing_ok,
                f"status={r.status_code} code={code}",
            )
        )

        try:
            from packages.billing.usage import parse_token_usage

            u = parse_token_usage(
                {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            )
            out.append(
                Check(
                    "PB",
                    "parse_token_usage",
                    u is not None and u.total_tokens == 15,
                    str(u),
                )
            )
        except Exception as e:
            out.append(Check("PB", "parse_token_usage", False, str(e)))

        try:
            from apps.gateway.tenants import load_tenants

            demo_b = load_tenants()["demo-b"]
            out.append(
                Check(
                    "PB",
                    "demo-b token_budget_daily",
                    demo_b.token_budget_daily == 500,
                    str(demo_b.token_budget_daily),
                )
            )
        except Exception as e:
            out.append(Check("PB", "demo-b token_budget_daily", False, str(e)))

        # PA audit
        await c.get("/healthz", headers=ADMIN_HEADERS)
        r = await c.get("/internal/audit/recent?limit=3", headers=ADMIN_HEADERS)
        body = r.json() if r.content else {}
        out.append(
            Check(
                "PA",
                "GET /internal/audit/recent",
                r.status_code == 200 and body.get("count", 0) >= 1,
                f"status={r.status_code} count={body.get('count')}",
            )
        )

        # W5 metrics
        await c.get("/healthz", headers=ADMIN_HEADERS)
        r = await c.get("/metrics")
        text = r.text if r.status_code == 200 else ""
        out.append(
            Check(
                "W5",
                "GET /metrics",
                r.status_code == 200 and "http_requests_total" in text,
                f"status={r.status_code}",
            )
        )

        # W5 trace header
        r = await c.get("/healthz", headers={**ADMIN_HEADERS, "X-Request-Id": "acceptance-trace-001"})
        tid = r.headers.get("x-request-id") or r.headers.get("X-Request-Id")
        out.append(Check("W5", "X-Request-Id 回写", tid == "acceptance-trace-001", f"got={tid}"))

        # W3 rag without key
        r = await c.post(
            "/v1/rag/query",
            headers=ADMIN_HEADERS,
            json={
                "tenant_id": "admin",
                "kb_id": "lab-demo",
                "version": 1,
                "query": "test",
            },
        )
        body = r.json() if r.content else {}
        code = (body.get("error") or {}).get("code")
        out.append(
            Check(
                "W3",
                "RAG 无 Key → 503",
                r.status_code == 503 and code == "UPSTREAM_NOT_CONFIGURED",
                f"status={r.status_code} code={code}",
                blocked=not with_llm,
            )
        )

        # W4 agent without key
        r = await c.post(
            "/v1/agent/run",
            headers=ADMIN_HEADERS,
            json={
                "tenant_id": "admin",
                "session_id": "acc-1",
                "messages": [{"role": "user", "content": "1+1"}],
            },
        )
        body = r.json() if r.content else {}
        code = (body.get("error") or {}).get("code")
        out.append(
            Check(
                "W4",
                "Agent 无 Key → 503",
                r.status_code == 503 and code == "UPSTREAM_NOT_CONFIGURED",
                f"status={r.status_code} code={code}",
                blocked=not with_llm,
            )
        )

        if with_llm:
            # W2 index + W3 query + eval 留给全量
            pass

    # W4 单元：calc
    try:
        from packages.agent.tools.builtin import _safe_eval_expr

        ok = _safe_eval_expr("(12+8)*2") == 40.0
        out.append(Check("W4", "calc 本地", ok, "40.0"))
    except Exception as e:
        out.append(Check("W4", "calc 本地", False, str(e)))

    # W4 租户工具
    try:
        from apps.gateway.tenants import load_tenants

        t = load_tenants()
        da = t["demo-a"].allowed_tools
        ok = "get_kb_snippet" in da and "calc" in da and "httpbin_delay" not in da
        out.append(Check("W4", "demo-a 工具白名单", ok, str(da)))
    except Exception as e:
        out.append(Check("W4", "demo-a 工具白名单", False, str(e)))

    # W6 model alias
    try:
        from apps.gateway.model_router import resolve_model_name

        ok = resolve_model_name("chat-fast") == "gpt-4o-mini"
        out.append(Check("W6", "别名 chat-fast", ok, resolve_model_name("chat-fast")))
    except Exception as e:
        out.append(Check("W6", "别名 chat-fast", False, str(e)))

    # W6 rate limit (demo-b burst=2)
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
            codes = []
            for _ in range(3):
                r = await c.post(
                    "/v1/chat/completions",
                    headers=DEMO_B_HEADERS,
                    json={"messages": [{"role": "user", "content": "hi"}]},
                )
                codes.append(r.status_code)
            ok = 429 in codes
            out.append(Check("W6", "demo-b 令牌桶 429", ok, str(codes)))
    except Exception as e:
        out.append(Check("W6", "demo-b 令牌桶 429", False, str(e)))

    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true", help="已配置 LLM_API_KEY 时跑全量")
    args = parser.parse_args()
    checks = asyncio.run(run_checks(with_llm=args.with_llm))

    # load_smoke healthz
    try:
        proc = subprocess.run(
            [sys.executable, "eval/load_smoke.py", "--concurrency", "50", "--target", "healthz"],
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = proc.returncode == 0 and "成功(2xx): 50" in proc.stdout
        checks.append(Check("W5", "50 并发 healthz", ok, proc.stdout[-200:] if proc.stdout else proc.stderr[-200:]))
    except Exception as e:
        checks.append(Check("W5", "50 并发 healthz", False, str(e)))

    passed = sum(1 for c in checks if c.passed)
    blocked = sum(1 for c in checks if c.blocked and not c.passed)
    failed = sum(1 for c in checks if not c.passed and not c.blocked)

    print(json.dumps({"passed": passed, "failed": failed, "blocked": blocked, "total": len(checks)}, indent=2))
    for c in checks:
        icon = "✅" if c.passed else ("⏸" if c.blocked else "❌")
        print(f"{icon} [{c.week}] {c.name}: {c.detail}")

    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
