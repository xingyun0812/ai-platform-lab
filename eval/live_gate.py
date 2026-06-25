#!/usr/bin/env python3
"""统一 Live 验收门禁 — 需 Gateway + LLM_API_KEY（无 Key 时 skip，不 fail）。

最佳实践：
- 本地/CI 无 Key：`python eval/live_gate.py run` → blocked，exit 0
- 严格模式：`python eval/live_gate.py run --require-live` → 无 Key 或失败则 exit 1
- GitHub Actions：`.github/workflows/live-gate.yml` workflow_dispatch + secrets
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
ADMIN_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@dataclass
class LiveCheck:
    name: str
    passed: bool
    detail: str
    blocked: bool = False


def _load_dotenv() -> None:
    env_path = REPO / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()


async def _healthz(client: httpx.AsyncClient) -> LiveCheck:
    try:
        r = await client.get("/healthz")
        return LiveCheck("gateway_healthz", r.status_code == 200, f"status={r.status_code}")
    except Exception as e:
        return LiveCheck("gateway_healthz", False, str(e))


async def _cot_live(client: httpx.AsyncClient, headers: dict[str, str]) -> LiveCheck:
    try:
        r = await client.post(
            "/v1/agent/run",
            headers=headers,
            json={
                "tenant_id": "admin",
                "session_id": "live-gate-cot",
                "messages": [{"role": "user", "content": "用一句话解释 2+2=4"}],
                "reasoning_mode": "cot",
                "model": "chat-fast",
            },
            timeout=120.0,
        )
        body = r.json() if r.content else {}
        trace = body.get("reasoning_trace") or []
        ok = (
            r.status_code in (200, 202)
            and body.get("status") in ("completed", "pending_approval")
            and (bool(trace) or len(str(body.get("final_message") or "")) > 5)
        )
        return LiveCheck(
            "agent_cot_live",
            ok,
            f"http={r.status_code} trace_len={len(trace)} status={body.get('status')}",
        )
    except Exception as e:
        return LiveCheck("agent_cot_live", False, str(e))


async def _multimodal_embed_live(client: httpx.AsyncClient, headers: dict[str, str]) -> LiveCheck:
    try:
        models_r = await client.get("/internal/embeddings/models", headers=headers)
        if models_r.status_code != 200:
            return LiveCheck(
                "multimodal_embed_live",
                False,
                f"models http={models_r.status_code}",
            )
        models_body = models_r.json() if models_r.content else {}
        models = models_body.get("models") if isinstance(models_body, dict) else models_body
        model_id = "stub-multimodal"
        if isinstance(models, list):
            ids = {m.get("model_id") for m in models if isinstance(m, dict)}
            if model_id not in ids and ids:
                model_id = sorted(ids)[0]

        r = await client.post(
            "/internal/embeddings/embed",
            headers=headers,
            json={
                "model_id": model_id,
                "inputs": [
                    {"type": "text", "text": "live gate multimodal"},
                    {"type": "image_base64", "data": TINY_PNG_B64, "mime": "image/png"},
                ],
            },
            timeout=60.0,
        )
        body = r.json() if r.content else {}
        embs = body.get("embeddings") if isinstance(body, dict) else None
        ok = r.status_code == 200 and isinstance(embs, list) and len(embs) >= 1
        dims = body.get("dimensions") if isinstance(body, dict) else "?"
        return LiveCheck(
            "multimodal_embed_live",
            ok,
            f"http={r.status_code} model={model_id} vectors={len(embs or [])} dim={dims}",
        )
    except Exception as e:
        return LiveCheck("multimodal_embed_live", False, str(e))


async def run_live_gate(
    *,
    base_url: str = DEFAULT_BASE,
    require_live: bool = False,
) -> list[LiveCheck]:
    from eval.auto_plan_vertical import run_auto_plan_vertical
    from eval.data_analysis_vertical import run_data_analysis_vertical
    from eval.phase_q_live import run_phase_q_live

    checks: list[LiveCheck] = []
    has_key = bool((os.environ.get("LLM_API_KEY") or "").strip())

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=180.0) as client:
        hz = await _healthz(client)
        checks.append(hz)
        if not hz.passed:
            return checks

    for qc in await run_phase_q_live(base_url=base_url):
        checks.append(
            LiveCheck(qc.name, qc.passed, qc.detail, blocked=qc.blocked)
        )

    if not has_key:
        checks.append(
            LiveCheck(
                "prerequisite",
                True,
                "skipped (无 LLM_API_KEY)",
                blocked=True,
            )
        )
        if require_live:
            checks[-1].passed = False
            checks[-1].detail = "required but LLM_API_KEY missing"
        return checks

    # 复用各 vertical 模块的 live 检查（跳过重复 healthz）
    for vc in await run_auto_plan_vertical(mock=False, live=True, base_url=base_url):
        if vc.name == "Gateway healthz":
            continue
        name = vc.name
        if name == "live orchestrator vertical 对照":
            name = "auto_plan_orchestrator_crosscheck"
        if name == "live auto_plan vertical":
            name = "auto_plan_vertical_live"
        checks.append(
            LiveCheck(name, vc.passed, vc.detail, blocked=vc.blocked)
        )

    for vc in await run_data_analysis_vertical(mock=False, live=True, base_url=base_url):
        if vc.name == "live orchestrator execute":
            checks.append(
                LiveCheck("data_analysis_orchestrator_live", vc.passed, vc.detail, vc.blocked)
            )

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=180.0) as client:
        checks.append(await _cot_live(client, ADMIN_HEADERS))
        checks.append(await _multimodal_embed_live(client, ADMIN_HEADERS))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="统一 Live 验收门禁")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="运行 live 检查")
    run_p.add_argument("--base-url", default=DEFAULT_BASE)
    run_p.add_argument(
        "--require-live",
        action="store_true",
        help="无 LLM_API_KEY 或任一项失败则 exit 1",
    )
    run_p.add_argument("--json", action="store_true")
    run_p.add_argument("--no-dotenv", action="store_true", help="不加载 .env（测试用）")
    args = parser.parse_args()

    if not args.no_dotenv:
        _load_dotenv()
    checks = asyncio.run(
        run_live_gate(base_url=args.base_url, require_live=args.require_live)
    )

    failed = sum(1 for c in checks if not c.passed and not c.blocked)
    blocked = sum(1 for c in checks if c.blocked)

    if args.json:
        print(
            json.dumps(
                {
                    "failed": failed,
                    "blocked": blocked,
                    "checks": [
                        {
                            "name": c.name,
                            "passed": c.passed,
                            "blocked": c.blocked,
                            "detail": c.detail,
                        }
                        for c in checks
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for c in checks:
            icon = "✅" if c.passed else ("⏸" if c.blocked else "❌")
            print(f"{icon} {c.name}: {c.detail}")
        print(
            json.dumps(
                {"failed": failed, "blocked": blocked, "total": len(checks)},
                ensure_ascii=False,
            )
        )

    if failed > 0:
        return 1
    if args.require_live and blocked > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
