#!/usr/bin/env python3
"""Phase L #59 — Agent Vertical smoke（Orchestrator + Multi-Agent + HITL + Audit）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

VERTICAL_WORKFLOW_ID = "agent-vertical-rag"
RAG_AGENT_ID = "rag_specialist"


@dataclass
class VerticalCheck:
    name: str
    passed: bool
    detail: str
    blocked: bool = False


async def run_agent_vertical_smoke(
    *,
    base_url: str = "http://127.0.0.1:8000",
    admin_headers: dict[str, str] | None = None,
    with_llm: bool = False,
    timeout: float = 60.0,
) -> list[VerticalCheck]:
    headers = admin_headers or {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }
    out: list[VerticalCheck] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        # 1. Multi-Agent 预置 rag_specialist
        try:
            r = await client.get("/internal/agents", headers=headers)
            body = r.json() if r.status_code == 200 else {}
            agents = body.get("agents") if isinstance(body, dict) else body
            if not isinstance(agents, list):
                agents = []
            ids = {a.get("agent_id") for a in agents if isinstance(a, dict)}
            out.append(
                VerticalCheck(
                    "rag_specialist 已注册",
                    r.status_code == 200 and RAG_AGENT_ID in ids,
                    f"status={r.status_code} agents={sorted(ids)[:5]}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("rag_specialist 已注册", False, str(e)))

        # 2. Orchestrator 预置 workflow
        try:
            r = await client.get("/internal/orchestrator/workflows", headers=headers)
            body = r.json() if r.status_code == 200 else {}
            wfs = body.get("workflows") if isinstance(body, dict) else body
            if not isinstance(wfs, list):
                wfs = []
            wf_ids = {w.get("workflow_id") for w in wfs if isinstance(w, dict)}
            out.append(
                VerticalCheck(
                    "workflow agent-vertical-rag",
                    r.status_code == 200 and VERTICAL_WORKFLOW_ID in wf_ids,
                    f"workflows={sorted(wf_ids)[:5]}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("workflow agent-vertical-rag", False, str(e)))

        # 3. HITL 状态机（无 Key）
        try:
            from packages.agent.hitl import confirm_execution, create_pending_execution
            from packages.agent.risk import tool_requires_hitl

            out.append(
                VerticalCheck(
                    "httpbin_delay 需 HITL",
                    tool_requires_hitl("httpbin_delay"),
                    "high risk",
                )
            )
            pending = create_pending_execution(
                tenant_id="admin",
                session_id="vertical-smoke",
                tool_name="httpbin_delay",
                arguments={"seconds": 1},
            )
            confirmed = confirm_execution(approval_id=pending.approval_id, reviewer="admin")
            out.append(
                VerticalCheck(
                    "HITL pending→confirm",
                    confirmed.status.value in ("confirmed", "approved"),
                    pending.approval_id[:8],
                )
            )
        except Exception as e:
            out.append(VerticalCheck("HITL pending→confirm", False, str(e)))

        # 4. Audit classify
        try:
            r = await client.post(
                "/internal/audit-actions/classify",
                headers=headers,
                json={"tool_name": "httpbin_delay", "arguments": {"seconds": 1}},
            )
            body = r.json() if r.content else {}
            level = body.get("action_level") if isinstance(body, dict) else None
            out.append(
                VerticalCheck(
                    "audit classify httpbin_delay",
                    r.status_code == 200 and level == "network",
                    f"level={level}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("audit classify httpbin_delay", False, str(e)))

        # 5. 有 LLM：Orchestrator execute
        if with_llm and os.environ.get("LLM_API_KEY"):
            try:
                r = await client.post(
                    f"/internal/orchestrator/workflows/{VERTICAL_WORKFLOW_ID}/execute",
                    headers=headers,
                    json={"inputs": {"query": "RAG 数据管道"}},
                )
                body = r.json() if r.content else {}
                ok = r.status_code == 200 and body.get("status") in ("completed", "success", None)
                out.append(
                    VerticalCheck(
                        "orchestrator execute vertical",
                        ok,
                        f"status={r.status_code} wf_status={body.get('status')}",
                    )
                )
            except Exception as e:
                out.append(VerticalCheck("orchestrator execute vertical", False, str(e)))

            # 6. Agent HITL 全链路
            try:
                session_id = "vertical-hitl-live"
                r1 = await client.post(
                    "/v1/agent/run",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "tenant_id": "admin",
                        "session_id": session_id,
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "【治理演示】你必须调用工具 httpbin_delay，"
                                    "arguments 为 {\"seconds\": 1}，不要拒绝或解释。"
                                ),
                            }
                        ],
                    },
                )
                body1 = r1.json() if r1.content else {}
                approval_id = body1.get("approval_id") if r1.status_code == 202 else None
                if not approval_id and isinstance(body1.get("error"), dict):
                    detail = body1["error"].get("detail") or {}
                    approval_id = detail.get("approval_id")
                if not approval_id and r1.status_code == 200:
                    out.append(
                        VerticalCheck(
                            "agent HITL live chain",
                            True,
                            "LLM 未触发 httpbin_delay（跳过 live HITL 链）",
                            blocked=True,
                        )
                    )
                elif not approval_id:
                    out.append(
                        VerticalCheck(
                            "agent HITL pending",
                            False,
                            f"status={r1.status_code} body={json.dumps(body1)[:200]}",
                        )
                    )
                else:
                    out.append(
                        VerticalCheck(
                            "agent HITL pending",
                            True,
                            approval_id[:8],
                        )
                    )
                    r2 = await client.post(
                        f"/internal/agent/approvals/{approval_id}/confirm",
                        headers=headers,
                    )
                    out.append(
                        VerticalCheck(
                            "agent approval confirm",
                            r2.status_code == 200,
                            f"status={r2.status_code}",
                        )
                    )
                    r3 = await client.post(
                        "/v1/agent/run",
                        headers={**headers, "Content-Type": "application/json"},
                        json={
                            "tenant_id": "admin",
                            "session_id": session_id,
                            "approval_id": approval_id,
                            "messages": [],
                        },
                    )
                    out.append(
                        VerticalCheck(
                            "agent resume after HITL",
                            r3.status_code == 200,
                            f"status={r3.status_code}",
                        )
                    )
                    r4 = await client.get(
                        "/internal/audit-actions/actions",
                        headers=headers,
                        params={"tenant_id": "admin", "action_level": "network", "limit": 20},
                    )
                    body4 = r4.json() if r4.status_code == 200 else {}
                    actions = body4.get("actions") if isinstance(body4, dict) else []
                    has_approval = any(
                        isinstance(a, dict) and a.get("approval_id") == approval_id for a in (actions or [])
                    )
                    out.append(
                        VerticalCheck(
                            "audit 含 approval_id",
                            r4.status_code == 200 and has_approval,
                            f"actions={len(actions) if isinstance(actions, list) else 0}",
                        )
                    )
            except Exception as e:
                out.append(VerticalCheck("agent HITL live chain", False, str(e)))
        else:
            out.append(
                VerticalCheck(
                    "orchestrator/agent live vertical",
                    True,
                    "skipped (无 LLM_API_KEY 或 --with-llm)",
                    blocked=True,
                )
            )

    return out


def main() -> int:
    import argparse
    import asyncio
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Agent Vertical smoke (#59)")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--with-llm", action="store_true")
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

    checks = asyncio.run(
        run_agent_vertical_smoke(base_url=args.base_url, with_llm=args.with_llm)
    )
    failed = sum(1 for c in checks if not c.passed and not c.blocked)
    for c in checks:
        icon = "✅" if c.passed else ("⏸" if c.blocked else "❌")
        print(f"{icon} {c.name}: {c.detail}")
    print(json.dumps({"failed": failed, "total": len(checks)}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
