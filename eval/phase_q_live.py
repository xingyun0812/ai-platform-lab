#!/usr/bin/env python3
"""Phase Q Live E2E — Gateway 端到端（export 无需 LLM；plan/approval 需 LLM_API_KEY）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

SAMPLE_PLAN = {
    "goal": "Phase Q live export smoke",
    "steps": [
        {
            "id": "s1",
            "description": "web search",
            "tool_hint": "web_search",
            "depends_on": [],
        },
        {
            "id": "s2",
            "description": "calc sum",
            "tool_hint": "calc",
            "depends_on": ["s1"],
        },
    ],
}

DEFAULT_HEADERS = {
    "X-Tenant-Id": "admin",
    "Authorization": "Bearer sk-tenant-admin-change-me",
}


@dataclass
class PhaseQLiveCheck:
    name: str
    passed: bool
    detail: str
    blocked: bool = False


async def run_phase_q_live(
    *,
    base_url: str = "http://127.0.0.1:8000",
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> list[PhaseQLiveCheck]:
    hdrs = headers or DEFAULT_HEADERS
    out: list[PhaseQLiveCheck] = []
    has_key = bool((os.environ.get("LLM_API_KEY") or "").strip())

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        # Q5 — export 不依赖 LLM，必须能测到 router 已挂载
        try:
            r = await client.post(
                "/v1/agent/plan/export",
                headers=hdrs,
                json={"plan": SAMPLE_PLAN},
            )
            body = r.text or ""
            ok = (
                r.status_code == 200
                and "text/yaml" in (r.headers.get("content-type") or "")
                and "plan_to_workflow" in body
                and "s1" in body
            )
            out.append(
                PhaseQLiveCheck(
                    "phase_q_plan_export_live",
                    ok,
                    f"http={r.status_code} bytes={len(body)}",
                )
            )
        except Exception as e:
            out.append(PhaseQLiveCheck("phase_q_plan_export_live", False, str(e)))

        # Q7 — orchestrator checkpoint（无需 LLM，依赖内置 data-analysis-vertical）
        try:
            r = await client.post(
                "/internal/orchestrator/workflows/data-analysis-vertical/execute",
                headers=hdrs,
                json={"inputs": {"topic": "Phase Q checkpoint live"}},
                timeout=timeout,
            )
            body = r.json() if r.content else {}
            eid = body.get("execution_id")
            ok_exec = r.status_code == 200 and bool(eid) and body.get("status") == "completed"
            cp_status = "n/a"
            ok_cp = False
            if ok_exec:
                r2 = await client.get(f"/internal/orchestrator/executions/{eid}", headers=hdrs)
                body2 = r2.json() if r2.content else {}
                cp_status = str(body2.get("status"))
                ok_cp = r2.status_code == 200 and body2.get("status") == "completed"
            out.append(
                PhaseQLiveCheck(
                    "phase_q_orchestrator_checkpoint_live",
                    ok_exec and ok_cp,
                    f"exec={r.status_code} eid={eid} cp_status={cp_status}",
                )
            )
        except Exception as e:
            out.append(PhaseQLiveCheck("phase_q_orchestrator_checkpoint_live", False, str(e)))

        if not has_key:
            out.append(
                PhaseQLiveCheck(
                    "phase_q_plan_generate_live",
                    True,
                    "skipped (无 LLM_API_KEY)",
                    blocked=True,
                )
            )
            out.append(
                PhaseQLiveCheck(
                    "phase_q_plan_approval_live",
                    True,
                    "skipped (无 LLM_API_KEY)",
                    blocked=True,
                )
            )
            return out

        # Q1 — structured plan via Gateway
        try:
            r = await client.post(
                "/v1/agent/plan",
                headers=hdrs,
                json={
                    "tenant_id": "admin",
                    "goal": "用 calc 计算 2+3，一步即可",
                    "model": "chat-fast",
                },
            )
            body = r.json() if r.content else {}
            plan = body.get("plan") or {}
            steps = plan.get("steps") if isinstance(plan, dict) else []
            ok = r.status_code == 200 and isinstance(steps, list) and len(steps) >= 1
            out.append(
                PhaseQLiveCheck(
                    "phase_q_plan_generate_live",
                    ok,
                    f"http={r.status_code} steps={len(steps) if isinstance(steps, list) else 0}",
                )
            )
        except Exception as e:
            out.append(PhaseQLiveCheck("phase_q_plan_generate_live", False, str(e)))

        # Q4 — plan-level HITL API 链（需 LLM 生成 plan）
        try:
            r = await client.post(
                "/v1/agent/run",
                headers=hdrs,
                json={
                    "tenant_id": "admin",
                    "session_id": "phase-q-live-approval",
                    "auto_plan": True,
                    "require_plan_approval": True,
                    "goal": "用 get_kb_snippet 查 RAG 再 calc 算 1+1",
                    "model": "chat-fast",
                },
            )
            body = r.json() if r.content else {}
            aid = body.get("plan_approval_id")
            status = body.get("status") or ""
            ok_pending = r.status_code in (200, 202) and status == "pending_plan_approval" and aid
            if not ok_pending:
                out.append(
                    PhaseQLiveCheck(
                        "phase_q_plan_approval_live",
                        False,
                        f"http={r.status_code} status={status} aid={aid}",
                    )
                )
                return out

            r2 = await client.get(f"/v1/agent/plan/approval/{aid}", headers=hdrs)
            body2 = r2.json() if r2.content else {}
            ok_get = r2.status_code == 200 and body2.get("status") == "pending"
            r3 = await client.post(
                f"/v1/agent/plan/approval/{aid}/approve",
                headers=hdrs,
            )
            body3 = r3.json() if r3.content else {}
            ok_approve = (
                ok_get
                and r3.status_code == 200
                and body3.get("status") == "approved"
            )
            if not ok_approve:
                out.append(
                    PhaseQLiveCheck(
                        "phase_q_plan_approval_live",
                        False,
                        f"approve failed get={r2.status_code} approve={r3.status_code}",
                    )
                )
                return out

            r4 = await client.post(
                "/v1/agent/run",
                headers=hdrs,
                json={
                    "tenant_id": "admin",
                    "session_id": "phase-q-live-approval",
                    "plan_approval_id": aid,
                    "model": "chat-fast",
                },
            )
            body4 = r4.json() if r4.content else {}
            ok_resume = (
                r4.status_code == 200
                and body4.get("resumed_from_plan_approval_id") == aid
                and body4.get("status") == "completed"
            )
            out.append(
                PhaseQLiveCheck(
                    "phase_q_plan_approval_resume_live",
                    ok_resume,
                    f"resume http={r4.status_code} status={body4.get('status')}",
                )
            )
        except Exception as e:
            out.append(PhaseQLiveCheck("phase_q_plan_approval_resume_live", False, str(e)))

    return out


if __name__ == "__main__":
    import argparse
    import asyncio
    import json
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    env_path = repo / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

    parser = argparse.ArgumentParser(description="Phase Q live E2E")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = asyncio.run(run_phase_q_live(base_url=args.base_url))
    failed = sum(1 for c in checks if not c.passed and not c.blocked)
    if args.json:
        print(
            json.dumps(
                {
                    "failed": failed,
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
    raise SystemExit(1 if failed else 0)
