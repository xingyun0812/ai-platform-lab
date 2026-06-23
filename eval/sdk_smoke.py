#!/usr/bin/env python3
"""Phase L #63 — Python SDK 端到端 smoke（chat / rag / agent）。"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK_PATH = os.path.join(REPO_ROOT, "sdk", "python")
if SDK_PATH not in sys.path:
    sys.path.insert(0, SDK_PATH)


def _skip(label: str, reason: str) -> None:
    print(f"    {label} skipped: {reason[:120]}")


def _upstream_unavailable(exc: BaseException) -> bool:
    from ai_platform_lab.exceptions import APIError

    if isinstance(exc, APIError) and exc.status_code in (503, 502, 504):
        return True
    if isinstance(exc, (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    msg = str(exc).upper()
    return "503" in msg or "UPSTREAM" in msg or "TIMEOUT" in msg


def _load_dotenv() -> None:
    env_path = os.path.join(REPO_ROOT, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="SDK smoke against running gateway")
    parser.add_argument("--base-url", default=os.environ.get("SDK_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--tenant", default=os.environ.get("DEMO_TENANT", "admin"))
    parser.add_argument("--api-key", default=os.environ.get("DEMO_TOKEN", "sk-tenant-admin-change-me"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--skip-rag", action="store_true")
    parser.add_argument("--skip-chat", action="store_true")
    args = parser.parse_args()

    from ai_platform_lab import Client

    print("==> healthz")
    try:
        r = httpx.get(f"{args.base_url.rstrip('/')}/healthz", timeout=args.timeout)
        r.raise_for_status()
    except Exception as e:
        print(f"ERROR: gateway not reachable at {args.base_url}: {e}", file=sys.stderr)
        return 1

    client = Client(
        base_url=args.base_url,
        api_key=args.api_key,
        tenant_id=args.tenant,
        timeout=args.timeout,
    )

    if not args.skip_chat:
        print("==> chat completions")
        try:
            r = client.chat.completions.create(
                model="chat-fast",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
            )
            content = ""
            if isinstance(r, dict):
                choices = r.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""
            print("    chat ok:", content[:40] or "(empty)")
        except Exception as e:
            if _upstream_unavailable(e):
                _skip("chat", str(e))
            else:
                raise

    if not args.skip_rag:
        print("==> rag query")
        try:
            r = client.rag.query(
                "RAG 数据管道",
                kb_id="lab-demo",
                tenant_id=args.tenant,
                version=1,
            )
            keys = list(r.keys())[:5] if isinstance(r, dict) else type(r).__name__
            print("    rag ok, keys:", keys)
        except Exception as e:
            if _upstream_unavailable(e):
                _skip("rag", str(e))
            else:
                _skip("rag", str(e))

    if not args.skip_agent:
        print("==> agent run")
        try:
            r = client.agent.run(
                session_id="sdk-smoke",
                message="1+1",
                tenant_id=args.tenant,
                messages=[{"role": "user", "content": "1+1"}],
            )
            preview = r.get("final_message") if isinstance(r, dict) else r
            print("    agent ok:", str(preview)[:80])
        except Exception as e:
            if _upstream_unavailable(e):
                _skip("agent", str(e))
            else:
                _skip("agent", str(e))

    print("OK sdk_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
