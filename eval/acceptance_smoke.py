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
            from packages.contracts.rag_schemas import RetrievedChunk
            from packages.rag.bm25_index import tokenize
            from packages.rag.hybrid import rrf_fusion

            toks = tokenize("RAG 数据管道 hello")
            ok = len(toks) >= 2
            c1 = RetrievedChunk(
                chunk_id="a", kb_id="k", version=1, source_uri="s", offset=0, text="t", score=0.9
            )
            c2 = RetrievedChunk(
                chunk_id="b", kb_id="k", version=1, source_uri="s", offset=0, text="t", score=0.8
            )
            fused = rrf_fusion(vector_chunks=[c1, c2], bm25_hits=[(c2, 1.0)], top_k=2, rrf_k=60)
            ok = ok and len(fused) == 2
            out.append(Check("PB2", "hybrid RRF 融合", ok, f"tokens={toks} fused={len(fused)}"))
        except Exception as e:
            out.append(Check("PB2", "hybrid RRF 融合", False, str(e)))

        try:
            import os

            from packages.secrets.provider import EnvSecretProvider

            os.environ["SECRET_TEST_KEY"] = "hello-secret"
            val = EnvSecretProvider().get_secret("env:SECRET_TEST_KEY")
            out.append(Check("PB2", "EnvSecretProvider", val == "hello-secret", val))
        except Exception as e:
            out.append(Check("PB2", "EnvSecretProvider", False, str(e)))

        try:
            from apps.gateway.settings import get_settings

            mode = get_settings().rag_retrieval_mode
            out.append(Check("PB2", "rag_retrieval_mode 默认 vector", mode == "vector", mode))
        except Exception as e:
            out.append(Check("PB2", "rag_retrieval_mode", False, str(e)))

        try:
            from packages.contracts.rag_schemas import RetrievedChunk
            from packages.rag.rerank import rerank_chunks

            hi = RetrievedChunk(
                chunk_id="a",
                kb_id="k",
                version=1,
                source_uri="s",
                offset=0,
                text="无关内容",
                score=0.9,
            )
            lo = RetrievedChunk(
                chunk_id="b",
                kb_id="k",
                version=1,
                source_uri="s",
                offset=0,
                text="RAG 数据管道说明",
                score=0.5,
            )
            reranked, _ms = rerank_chunks("RAG 数据管道", [hi, lo], top_n=2)
            out.append(
                Check(
                    "PB3",
                    "rerank stub 重排",
                    reranked[0].chunk_id == "b",
                    f"top={reranked[0].chunk_id}",
                )
            )
        except Exception as e:
            out.append(Check("PB3", "rerank stub 重排", False, str(e)))

        try:
            from packages.rag.routing import KbRoutingRule, pick_query_version, routing_bucket

            rules = {
                "lab-demo": KbRoutingRule(stable_version=1, canary_version=2, canary_percent=30),
            }

            def _versions(_kb: str) -> list[int]:
                return [1, 2]

            canary_n = 0
            for i in range(200):
                _ver, route, _ = pick_query_version(
                    "lab-demo",
                    None,
                    tenant_id="admin",
                    query=f"probe-{i}",
                    rules=rules,
                    list_versions=_versions,
                )
                if route == "canary":
                    canary_n += 1
            rate = canary_n / 200
            out.append(
                Check(
                    "PB3",
                    "canary 30% 分桶",
                    0.20 <= rate <= 0.42,
                    f"rate={rate:.2f}",
                )
            )
            b1 = routing_bucket("admin", "same-query")
            b2 = routing_bucket("admin", "same-query")
            zero_rules = {"lab-demo": KbRoutingRule(canary_version=2, canary_percent=0)}
            stable_only = all(
                pick_query_version(
                    "lab-demo",
                    None,
                    tenant_id="admin",
                    query=f"z-{i}",
                    rules=zero_rules,
                    list_versions=_versions,
                )[1]
                == "stable"
                for i in range(20)
            )
            out.append(
                Check(
                    "PB3",
                    "canary_percent=0 全 stable",
                    stable_only and b1 == b2,
                    f"stable_only={stable_only} bucket={b1}",
                )
            )
        except Exception as e:
            out.append(Check("PB3", "canary 路由", False, str(e)))

        try:
            from packages.providers.registry import get_provider_matrix, pick_provider_for_model

            matrix = get_provider_matrix()
            mini = pick_provider_for_model("gpt-4o-mini")
            ok = len(matrix.offerings) >= 2 and mini is not None
            r = await c.get("/internal/providers/matrix", headers=ADMIN_HEADERS)
            body = r.json() if r.content else {}
            ok = ok and r.status_code == 200 and len(body.get("offerings", [])) >= 2
            out.append(
                Check(
                    "PC",
                    "providers matrix",
                    ok,
                    f"policy={body.get('routing_policy')} n={len(body.get('offerings', []))}",
                )
            )
        except Exception as e:
            out.append(Check("PC", "providers matrix", False, str(e)))

        try:
            from packages.region.resolver import RegionViolation, resolve_region

            r = await c.get("/internal/regions", headers=ADMIN_HEADERS)
            ok = r.status_code == 200 and len((r.json() or {}).get("regions", [])) >= 2
            try:
                resolve_region(header_region="eu-de", tenant_home_region=None, tenant_data_zone="CN")
                viol = False
            except RegionViolation:
                viol = True
            out.append(
                Check(
                    "PC",
                    "regions + 驻留校验",
                    ok and viol,
                    f"regions_status={r.status_code} viol={viol}",
                )
            )
        except Exception as e:
            out.append(Check("PC", "regions", False, str(e)))

        try:
            r = await c.get("/internal/tenants/demo-a/profile", headers=ADMIN_HEADERS)
            body = r.json() if r.content else {}
            ok = (
                r.status_code == 200
                and body.get("tenant_id") == "demo-a"
                and body.get("data_zone") == "CN"
            )
            out.append(
                Check(
                    "PC",
                    "tenant profile",
                    ok,
                    f"zone={body.get('data_zone')}",
                )
            )
        except Exception as e:
            out.append(Check("PC", "tenant profile", False, str(e)))

        try:
            from packages.agent.marketplace import catalog_payload

            cat = catalog_payload()
            r = await c.get("/internal/tools/marketplace", headers=DEMO_A_HEADERS)
            ok = r.status_code == 200 and len(cat.get("tools", [])) >= 3
            out.append(
                Check(
                    "PC",
                    "tools marketplace",
                    ok,
                    f"tools={len(cat.get('tools', []))}",
                )
            )
        except Exception as e:
            out.append(Check("PC", "tools marketplace", False, str(e)))

        try:
            from packages.router.circuit_breaker import CircuitBreaker

            cb = CircuitBreaker(failure_threshold=2, recovery_seconds=60)
            cb.record_failure("m")
            cb.record_failure("m")
            allowed, state = cb.allow("m")
            out.append(Check("PD", "熔断器 open", not allowed and state == "open", state))
        except Exception as e:
            out.append(Check("PD", "熔断器", False, str(e)))

        try:
            import base64
            import hashlib
            import hmac
            import json

            from packages.auth.jwt_hs256 import decode_hs256

            secret = "test-secret"
            header = base64.urlsafe_b64encode(
                json.dumps({"alg": "HS256"}).encode()
            ).decode().rstrip("=")
            payload = base64.urlsafe_b64encode(
                json.dumps({"tenant_id": "admin", "role": "platform_admin"}).encode()
            ).decode().rstrip("=")
            sig = base64.urlsafe_b64encode(
                hmac.new(
                    secret.encode(),
                    f"{header}.{payload}".encode(),
                    hashlib.sha256,
                ).digest()
            ).decode().rstrip("=")
            token = f"{header}.{payload}.{sig}"
            claims = decode_hs256(token, secret)
            out.append(
                Check(
                    "PD",
                    "JWT HS256",
                    claims is not None and claims.get("role") == "platform_admin",
                    str(claims),
                )
            )
        except Exception as e:
            out.append(Check("PD", "JWT HS256", False, str(e)))

        try:
            from packages.auth.rbac import can_patch_tenant_limits

            out.append(
                Check(
                    "PD",
                    "RBAC platform_admin",
                    can_patch_tenant_limits("platform_admin")
                    and not can_patch_tenant_limits("viewer"),
                    "ok",
                )
            )
        except Exception as e:
            out.append(Check("PD", "RBAC", False, str(e)))

        try:
            from packages.billing.cost import estimate_cost_usd

            cost = estimate_cost_usd(model="gpt-4o-mini", input_tokens=1000, output_tokens=500)
            out.append(Check("PD", "分价估算", cost > 0, f"usd={cost}"))
        except Exception as e:
            out.append(Check("PD", "分价估算", False, str(e)))

        try:
            from packages.agent.mcp_stub import load_mcp_stub_tools

            tools = load_mcp_stub_tools()
            out.append(Check("PD", "MCP stub 工具", "mcp_echo" in tools, list(tools.keys())))
        except Exception as e:
            out.append(Check("PD", "MCP stub", False, str(e)))

        try:
            from packages.rag.canary_guard import apply_auto_rollback

            r = await c.get("/internal/providers/matrix", headers=ADMIN_HEADERS)
            out.append(Check("PD", "GET providers matrix", r.status_code == 200, str(r.status_code)))
            _ = apply_auto_rollback  # 函数可导入
        except Exception as e:
            out.append(Check("PD", "providers API", False, str(e)))

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

    # Phase E1 agent eval 结构 + 轨迹评估逻辑
    try:
        from pathlib import Path

        from eval.agent_run import evaluate_agent_case, validate_agent_baseline

        repo = Path(__file__).resolve().parents[1]
        ok, errors = validate_agent_baseline(repo / "eval" / "agent_baseline.jsonl")
        out.append(Check("PE", "agent_baseline 格式", ok, "; ".join(errors[:3]) if errors else "5 cases"))

        passed, reason, _ = evaluate_agent_case(
            {
                "expect_tools": ["calc"],
                "forbid_tools": ["get_kb_snippet"],
                "expect_first_tool": "calc",
            },
            status=200,
            body={
                "final_message": "40",
                "tool_calls": [{"tool_name": "calc", "status": "success", "arguments": {}}],
            },
        )
        out.append(Check("PE", "轨迹评估 calc 命中", passed, reason))

        passed2, reason2, _ = evaluate_agent_case(
            {"forbid_tools": ["calc"], "expect_no_tools": True},
            status=200,
            body={"final_message": "hi", "tool_calls": [{"tool_name": "calc", "status": "success"}]},
        )
        out.append(Check("PE", "轨迹评估 禁止工具", not passed2, reason2))

        passed3, reason3, _ = evaluate_agent_case(
            {"expect": "error", "expect_error_code": "AGENT_TOOL_FORBIDDEN"},
            status=403,
            body={"error": {"code": "AGENT_TOOL_FORBIDDEN", "message": "forbidden"}},
        )
        out.append(Check("PE", "轨迹评估 403 错误码", passed3, reason3))
    except Exception as e:
        out.append(Check("PE", "agent eval 逻辑", False, str(e)))

    # Phase E2 tool routing
    try:
        from packages.agent.registry import ToolRegistry
        from packages.agent.tool_router import select_tools_for_query

        reg = ToolRegistry()
        kb = select_tools_for_query(
            "请查知识库 RAG 管道",
            registry=reg,
            allowed_tools=(),
            routing_enabled=True,
        )
        ok_kb = "get_kb_snippet" in kb.tool_names and "search_web_stub" not in kb.tool_names
        out.append(
            Check(
                "PE",
                "Tool 路由 kb_query",
                ok_kb,
                f"tools={list(kb.tool_names)} intent={kb.intent}",
            )
        )
        calc = select_tools_for_query(
            "用 calc 计算 1+2",
            registry=reg,
            allowed_tools=(),
            routing_enabled=True,
        )
        ok_calc = "calc" in calc.tool_names and "math_llm_stub" not in calc.tool_names
        out.append(
            Check(
                "PE",
                "Tool 路由 calc",
                ok_calc,
                f"tools={list(calc.tool_names)} intent={calc.intent}",
            )
        )
    except Exception as e:
        out.append(Check("PE", "Tool 路由", False, str(e)))

    # Phase E3 context budget + session compact
    try:
        from packages.agent.context_budget import (
            assemble_llm_messages,
            maybe_compact_session,
            truncate_tool_content,
        )
        from packages.agent.session import SessionStore
        from packages.agent.session_state import SessionState, parse_session_raw, serialize_session

        long_tool = "x" * 5000
        trimmed, did = truncate_tool_content(long_tool, 2000)
        out.append(Check("PE", "tool 结果截断", did and len(trimmed) < len(long_tool), f"len={len(trimmed)}"))

        state = SessionState(
            messages=[{"role": "user", "content": "a"}],
            summary=None,
            turn_count=6,
        )
        turns = []
        for i in range(8):
            turns.append({"role": "user", "content": f"turn-{i}"})
            turns.append({"role": "assistant", "content": f"reply-{i}"})
        compacted = maybe_compact_session(
            SessionState(messages=turns, summary=None, turn_count=6),
            every_n_turns=6,
            keep_recent_turns=2,
        )
        out.append(
            Check(
                "PE",
                "滚动摘要 compact",
                len(compacted.messages) < len(turns) and compacted.summary,
                f"msgs={len(compacted.messages)} summary={bool(compacted.summary)}",
            )
        )

        assembled, meta = assemble_llm_messages(
            SessionState(messages=[{"role": "user", "content": "y" * 20000}], summary="old"),
            [{"role": "user", "content": "new"}],
            budget=500,
            keep_recent_turns=2,
            tool_result_max_chars=1000,
        )
        out.append(
            Check(
                "PE",
                "Token 预算裁剪",
                meta.truncated_messages > 0 or meta.estimated_tokens <= 500,
                f"tokens={meta.estimated_tokens} dropped={meta.truncated_messages}",
            )
        )

        store = SessionStore()
        store.save_session_state("t1", "s1", SessionState(messages=[{"role": "user", "content": "hi"}], turn_count=2))
        loaded = store.get_session_state("t1", "s1")
        out.append(Check("PE", "SessionState 读写", loaded.turn_count == 2, f"turns={loaded.turn_count}"))

        roundtrip = parse_session_raw(json.loads(serialize_session(loaded)))
        out.append(Check("PE", "Session 序列化", roundtrip.turn_count == 2, "ok"))
    except Exception as e:
        out.append(Check("PE", "context budget", False, str(e)))

    # Phase E4 quality gate
    try:
        from packages.agent.quality_gate import assess_tool_output
        from packages.agent.tool_envelope import failure_envelope, parse_tool_result, success_envelope

        env = parse_tool_result(success_envelope({"result": 2}, quality_score=1.0))
        out.append(Check("PE", "tool envelope 解析", env.ok and env.quality_score == 1.0, "ok"))

        _, gate_low = assess_tool_output(
            "get_kb_snippet",
            success_envelope({"snippets": []}, quality_score=0.0),
            min_score=0.3,
        )
        out.append(Check("PE", "KB 空结果 low_quality", gate_low == "low_quality", gate_low))

        _, gate_fail = assess_tool_output(
            "calc",
            failure_envelope(error_code="ERR", message="bad"),
            min_score=0.3,
        )
        out.append(Check("PE", "envelope failed", gate_fail == "failed", gate_fail))
    except Exception as e:
        out.append(Check("PE", "quality gate", False, str(e)))

    # Phase E5 HITL + shadow
    try:
        from packages.agent.hitl import confirm_execution, create_pending_execution
        from packages.agent.risk import tool_requires_hitl
        from packages.agent.shadow import shadow_tool_record

        out.append(Check("PE", "httpbin HITL", tool_requires_hitl("httpbin_delay"), "high risk"))

        pending = create_pending_execution(
            tenant_id="admin",
            session_id="smoke-hitl",
            tool_name="httpbin_delay",
            arguments={"seconds": 1},
        )
        confirmed = confirm_execution(approval_id=pending.approval_id, reviewer="admin")
        out.append(
            Check(
                "PE",
                "HITL confirm",
                confirmed.status.value == "confirmed",
                confirmed.approval_id[:8],
            )
        )

        payload, rec = shadow_tool_record(tool_name="calc", arguments={"expression": "1+1"})
        out.append(
            Check(
                "PE",
                "Shadow 工具记录",
                rec.status == "success" and "shadow" in payload,
                rec.tool_name,
            )
        )
    except Exception as e:
        out.append(Check("PE", "HITL/shadow", False, str(e)))

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
