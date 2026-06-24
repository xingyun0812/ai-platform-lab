#!/usr/bin/env python3
"""O1 + 数据分析 Vertical 端到端闭环 — Plan 驱动 web_search → sql_query → calc。

与 `data_analysis_vertical.py`（Orchestrator 固定 DAG）互补：
本脚本验证 **LLM Plan + auto_plan 逐步执行** 能跑通同一业务工具链。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpx
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DATA_ANALYSIS_WORKFLOW_ID = "data-analysis-vertical"
WORKFLOW_YAML = REPO / "config" / "workflows" / "data_analysis.yaml"

VERTICAL_GOAL = (
    "数据分析：搜索 enterprise SaaS analytics 背景，"
    "对 demo_sales 按 region SQL 聚合，再 calc 计算同比增幅"
)

VERTICAL_PLAN: dict = {
    "goal": VERTICAL_GOAL,
    "steps": [
        {
            "id": "s1",
            "description": "检索 AI analytics 市场背景",
            "tool_hint": "web_search",
            "depends_on": [],
        },
        {
            "id": "s2",
            "description": "SQL 聚合 demo_sales 按 region",
            "tool_hint": "sql_query",
            "depends_on": ["s1"],
        },
        {
            "id": "s3",
            "description": "计算同比增幅百分比",
            "tool_hint": "calc",
            "depends_on": ["s2"],
        },
    ],
}

VERTICAL_TOOL_ARGS: dict[str, dict] = {
    "web_search": {"query": "enterprise SaaS analytics trends", "top_k": 3},
    "sql_query": {
        "sql": (
            "SELECT region, SUM(amount) AS total "
            "FROM demo_sales GROUP BY region LIMIT 10"
        ),
    },
    "calc": {"expression": "(14100 - 12000) / 12000 * 100"},
}


@dataclass
class VerticalCheck:
    name: str
    passed: bool
    detail: str
    blocked: bool = False


def _tool_hint_from_step_message(content: str) -> str | None:
    m = re.search(r"建议工具：(\w+)", content)
    return m.group(1) if m else None


async def _vertical_step_runner(**kwargs) -> dict:
    """模拟每步 ReAct 直接执行 Plan 建议工具（离线，真实 tool handler）。"""
    from packages.agent.registry import ToolRegistry
    from packages.agent.runner import _execute_tool

    content = kwargs["new_messages"][-1]["content"]
    tool_name = _tool_hint_from_step_message(content)
    if not tool_name:
        return {
            "final_message": "no tool_hint",
            "tool_calls": [],
            "steps": 0,
            "model": "vertical-mock",
            "status": "failed",
        }

    args = VERTICAL_TOOL_ARGS.get(tool_name, {})
    registry = ToolRegistry()
    result, record = await _execute_tool(
        registry,
        tool_name=tool_name,
        arguments_json=json.dumps(args, ensure_ascii=False),
        allowed_tools=kwargs.get("allowed_tools", ()),
        tool_timeout=30.0,
        tool_max_retries=1,
        tenant_id=kwargs.get("tenant_id", ""),
        session_id=kwargs.get("session_id", ""),
        skip_hitl=True,
    )
    return {
        "final_message": f"[{tool_name}] {str(result)[:400]}",
        "tool_calls": [record],
        "steps": 1,
        "model": "vertical-mock",
        "status": "completed",
    }


class _MemSessionStore:
    def get_session_state(self, tenant_id: str, session_id: str):
        from packages.agent.session_state import SessionState

        return SessionState(messages=[], summary=None, turn_count=0)

    def save_session_state(self, tenant_id: str, session_id: str, state) -> None:
        return None


async def run_mock_checks() -> list[VerticalCheck]:
    from packages.agent.planner import execute_plan_with_agent, generate_plan, parse_plan
    from packages.contracts.agent_schemas import ToolCallRecord

    out: list[VerticalCheck] = []

    # 与 Orchestrator vertical YAML 工具链对齐
    try:
        data = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
        wfs = data.get("workflows") if isinstance(data, dict) else []
        wf = next(
            (w for w in (wfs or []) if isinstance(w, dict) and w.get("workflow_id") == DATA_ANALYSIS_WORKFLOW_ID),
            None,
        )
        node_tools = {
            n.get("config", {}).get("tool_name")
            for n in (wf or {}).get("nodes", [])
            if isinstance(n, dict) and n.get("node_type") == "tool_call"
        }
        expected = {"web_search", "sql_query", "calc"}
        ok = expected <= node_tools
        out.append(
            VerticalCheck(
                "vertical YAML 工具链对齐",
                ok,
                f"workflow_tools={sorted(node_tools)}",
            )
        )
    except Exception as e:
        out.append(VerticalCheck("vertical YAML 工具链对齐", False, str(e)))

    plan = parse_plan(VERTICAL_PLAN)

    try:
        result = await execute_plan_with_agent(
            plan=plan,
            tenant_id="admin",
            session_id="auto-plan-vertical-mock",
            allowed_tools=(),
            allowed_models=(),
            model="chat-fast",
            session_store=_MemSessionStore(),
            run_agent_fn=_vertical_step_runner,
        )
        tool_names = [
            tc.tool_name if isinstance(tc, ToolCallRecord) else tc.get("tool_name")
            for tc in (result.get("tool_calls") or [])
        ]
        ok = (
            result.get("plan_steps_completed") == 3
            and result.get("status") == "completed"
            and {"web_search", "sql_query", "calc"} <= set(tool_names)
            and "demo_sales" in str(result.get("final_message", "")).lower()
            + str(result.get("tool_calls", "")).lower()
        )
        out.append(
            VerticalCheck(
                "auto_plan 三步 tool 链 mock 执行",
                ok,
                f"steps={result.get('plan_steps_completed')} tools={tool_names}",
            )
        )
    except Exception as e:
        out.append(VerticalCheck("auto_plan 三步 tool 链 mock 执行", False, str(e)))

    # generate_plan → execute 全链路（mock LLM）
    try:
        async def _fake_route(_body):
            class R:
                status = 200
                body = {
                    "choices": [
                        {"message": {"content": json.dumps(VERTICAL_PLAN, ensure_ascii=False)}}
                    ]
                }
                error = None

            return R()

        async def _run_gen() -> dict:
            with patch("packages.agent.planner.forward_with_model_router", _fake_route):
                plan2, _ = await generate_plan(
                    goal=VERTICAL_GOAL,
                    allowed_models=(),
                    allowed_tools=(),
                )
            return await execute_plan_with_agent(
                plan=plan2,
                tenant_id="admin",
                session_id="auto-plan-vertical-gen",
                allowed_tools=(),
                allowed_models=(),
                model="chat-fast",
                session_store=_MemSessionStore(),
                run_agent_fn=_vertical_step_runner,
            )

        gen_result = await _run_gen()
        ok = gen_result.get("plan_steps_completed") == 3 and len(gen_result.get("tool_calls") or []) == 3
        out.append(
            VerticalCheck(
                "generate_plan + execute 全链路 mock",
                ok,
                f"plan_steps={gen_result.get('plan_steps_completed')}",
            )
        )
    except Exception as e:
        out.append(VerticalCheck("generate_plan + execute 全链路 mock", False, str(e)))

    return out


async def run_live_checks(
    *,
    base_url: str = "http://127.0.0.1:8000",
    admin_headers: dict[str, str] | None = None,
    timeout: float = 180.0,
) -> list[VerticalCheck]:
    headers = admin_headers or {
        "X-Tenant-Id": "admin",
        "Authorization": "Bearer sk-tenant-admin-change-me",
    }
    out: list[VerticalCheck] = []

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        try:
            r = await client.get("/healthz")
            out.append(
                VerticalCheck("Gateway healthz", r.status_code == 200, f"status={r.status_code}")
            )
        except Exception as e:
            out.append(VerticalCheck("Gateway healthz", False, str(e)))
            return out

        session_id = "auto-plan-vertical-live"
        payload = {
            "tenant_id": "admin",
            "session_id": session_id,
            "auto_plan": True,
            "goal": VERTICAL_GOAL,
            "model": "chat-fast",
        }
        try:
            r = await client.post("/v1/agent/run", headers=headers, json=payload)
            body = r.json() if r.content else {}
            plan = body.get("plan") or {}
            steps = plan.get("steps") if isinstance(plan, dict) else []
            tool_calls = body.get("tool_calls") or []
            tool_names = {tc.get("tool_name") for tc in tool_calls if isinstance(tc, dict)}
            completed = int(body.get("plan_steps_completed") or 0)
            status = body.get("status") or ""
            final_msg = str(body.get("final_message") or "")

            ok = (
                r.status_code in (200, 202)
                and isinstance(steps, list)
                and len(steps) >= 2
                and completed >= 1
                and status in ("completed", "pending_approval")
                and (tool_names or len(final_msg) > 10)
            )
            out.append(
                VerticalCheck(
                    "live auto_plan vertical",
                    ok,
                    (
                        f"http={r.status_code} plan_steps={len(steps)} "
                        f"completed={completed} tools={sorted(tool_names)} status={status}"
                    ),
                )
            )
        except Exception as e:
            out.append(VerticalCheck("live auto_plan vertical", False, str(e)))

        # 对照：Orchestrator vertical 仍可执行（双路径共存）
        try:
            r = await client.post(
                f"/internal/orchestrator/workflows/{DATA_ANALYSIS_WORKFLOW_ID}/execute",
                headers=headers,
                json={"inputs": {"topic": "AI platform market 2024"}},
            )
            body = r.json() if r.content else {}
            ok = r.status_code == 200 and body.get("status") == "completed"
            out.append(
                VerticalCheck(
                    "live orchestrator vertical 对照",
                    ok,
                    f"status={r.status_code} wf={body.get('status')}",
                )
            )
        except Exception as e:
            out.append(VerticalCheck("live orchestrator vertical 对照", False, str(e)))

    return out


async def run_auto_plan_vertical(
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
                    "live auto_plan vertical",
                    True,
                    "skipped (无 LLM_API_KEY)",
                    blocked=True,
                )
            )
        else:
            checks.extend(await run_live_checks(base_url=base_url))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="O1 auto_plan + 数据分析 vertical E2E")
    parser.add_argument("--mock", action="store_true", default=True, help="离线 mock（默认）")
    parser.add_argument("--live", action="store_true", help="需 Gateway + LLM_API_KEY")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()

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
        run_auto_plan_vertical(
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
