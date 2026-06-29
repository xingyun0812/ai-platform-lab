#!/usr/bin/env python3
"""Phase O #93 — 数据分析 Vertical smoke（web_search → sql_query → calc）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_ANALYSIS_WORKFLOW_ID = "data-analysis-vertical"
DATA_ANALYST_AGENT_ID = "data_analyst"
WORKFLOW_YAML = REPO / "config" / "workflows" / "data_analysis.yaml"


@dataclass
class VerticalCheck:
    name: str
    passed: bool
    detail: str
    blocked: bool = False


def _load_workflow_store():
    from packages.agent.orchestrator.workflow_store import WorkflowStore

    return WorkflowStore(
        yaml_path=REPO / "config" / "orchestrator_workflows.yaml",
        extra_workflows_dir=REPO / "config" / "workflows",
    )


async def run_mock_checks() -> list[VerticalCheck]:
    out: list[VerticalCheck] = []

    try:
        data = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
        wfs = data.get("workflows") if isinstance(data, dict) else []
        ok = isinstance(wfs, list) and any(
            isinstance(w, dict) and w.get("workflow_id") == DATA_ANALYSIS_WORKFLOW_ID for w in wfs
        )
        out.append(VerticalCheck("data_analysis.yaml 可解析", ok, f"workflows={len(wfs or [])}"))
    except Exception as e:
        out.append(VerticalCheck("data_analysis.yaml 可解析", False, str(e)))

    try:
        store = _load_workflow_store()
        store.load()
        wf = store.get_workflow(DATA_ANALYSIS_WORKFLOW_ID)
        out.append(
            VerticalCheck(
                "workflow 已注册",
                wf is not None,
                DATA_ANALYSIS_WORKFLOW_ID if wf else "missing",
            )
        )
    except Exception as e:
        out.append(VerticalCheck("workflow 已注册", False, str(e)))
        wf = None

    try:
        agents = yaml.safe_load((REPO / "config" / "agents.yaml").read_text(encoding="utf-8"))
        ids = {a.get("agent_id") for a in agents.get("agents", []) if isinstance(a, dict)}
        spec = next(
            (a for a in agents.get("agents", []) if a.get("agent_id") == DATA_ANALYST_AGENT_ID),
            None,
        )
        tools = spec.get("allowed_tools") if isinstance(spec, dict) else []
        ok = DATA_ANALYST_AGENT_ID in ids and {"web_search", "sql_query", "calc"} <= set(tools or [])
        out.append(
            VerticalCheck(
                "data_analyst agent spec",
                ok,
                f"tools={sorted(tools or [])[:5]}",
            )
        )
    except Exception as e:
        out.append(VerticalCheck("data_analyst agent spec", False, str(e)))

    if wf is not None:
        try:
            from packages.agent.orchestrator.engine import execute_workflow

            result = await execute_workflow(
                wf,
                inputs={"topic": "enterprise SaaS analytics trends"},
                timeout_seconds=30.0,
            )
            report = str((result.outputs.get("report") or {}).get("value") or "")
            ok = (
                result.status == "completed"
                and "demo_sales" in report.lower() + str(result.outputs).lower()
                and "web_search" in str(result.outputs).lower()
            )
            out.append(
                VerticalCheck(
                    "orchestrator tool 链 mock 执行",
                    ok,
                    f"status={result.status} len={len(report)}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("orchestrator tool 链 mock 执行", False, str(e)))

    return out


async def run_live_checks(
    *,
    base_url: str = "http://127.0.0.1:8000",
    admin_headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> list[VerticalCheck]:
    headers = admin_headers or {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }
    out: list[VerticalCheck] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        try:
            r = await client.get("/internal/orchestrator/workflows", headers=headers)
            body = r.json() if r.status_code == 200 else {}
            wfs = body.get("workflows") if isinstance(body, dict) else []
            ids = {w.get("workflow_id") for w in wfs if isinstance(w, dict)}
            out.append(
                VerticalCheck(
                    "API workflow 列表含 vertical",
                    r.status_code == 200 and DATA_ANALYSIS_WORKFLOW_ID in ids,
                    f"workflows={sorted(ids)[:6]}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("API workflow 列表含 vertical", False, str(e)))

        session_id = "data-analysis-vertical-live"
        try:
            r = await client.post(
                f"/internal/orchestrator/workflows/{DATA_ANALYSIS_WORKFLOW_ID}/execute",
                headers=headers,
                json={"inputs": {"topic": "AI platform market 2024"}},
            )
            body = r.json() if r.content else {}
            report = str(body.get("final_output") or "")
            ok = r.status_code == 200 and body.get("status") == "completed" and len(report) > 20
            out.append(
                VerticalCheck(
                    "live orchestrator execute",
                    ok,
                    f"status={r.status_code} wf={body.get('status')}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("live orchestrator execute", False, str(e)))

        try:
            r = await client.get(
                f"/v1/agent/blackboard/{session_id}",
                headers=headers,
            )
            body = r.json() if r.content else {}
            out.append(
                VerticalCheck(
                    "blackboard API 可达",
                    r.status_code == 200 and isinstance(body.get("entries"), list),
                    f"count={body.get('count')}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("blackboard API 可达", False, str(e)))

    return out


async def run_data_analysis_vertical(
    *,
    mock: bool = True,
    live: bool = False,
    base_url: str = "http://127.0.0.1:8000",
) -> list[VerticalCheck]:
    checks: list[VerticalCheck] = []
    if mock:
        checks.extend(await run_mock_checks())
    if live:
        if not os.environ.get("LLM_API_KEY"):
            checks.append(
                VerticalCheck(
                    "live orchestrator execute",
                    True,
                    "skipped (无 LLM_API_KEY)",
                    blocked=True,
                )
            )
        else:
            checks.extend(await run_live_checks(base_url=base_url))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Data analysis vertical smoke (#93)")
    parser.add_argument("--mock", action="store_true", default=True, help="离线 mock（默认）")
    parser.add_argument("--live", action="store_true", help="需 Gateway + LLM_API_KEY")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()

    from eval.platform_wire import ensure_platform_wired

    ensure_platform_wired()

    env_path = REPO / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()

    checks = asyncio.run(
        run_data_analysis_vertical(
            mock=args.mock or not args.live,
            live=args.live,
            base_url=args.base_url,
        )
    )
    failed = sum(1 for c in checks if not c.passed and not c.blocked)
    for c in checks:
        icon = "✅" if c.passed else ("⏸" if c.blocked else "❌")
        print(f"{icon} {c.name}: {c.detail}")
    print(json.dumps({"failed": failed, "total": len(checks)}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
