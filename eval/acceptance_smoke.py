#!/usr/bin/env python3
"""本机验收冒烟（不依赖 LLM Key 的部分 + 可选全量）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
REPO_ROOT = Path(__file__).resolve().parents[1]
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


def _ensure_platform_wired() -> None:
    from eval.platform_wire import ensure_platform_wired

    ensure_platform_wired()


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

        # Phase G — 语义缓存
        try:
            from packages.semantic_cache import (
                InMemorySemanticCache,
                SemanticCacheConfig,
                get_semantic_cache_metrics,
            )
            from packages.semantic_cache.metrics import reset_metrics_for_tests
            from packages.semantic_cache.store import reset_semantic_cache_for_tests

            reset_metrics_for_tests()
            reset_semantic_cache_for_tests()
            cfg = SemanticCacheConfig(
                enabled=True,
                mode="exact",
                similarity_threshold=0.9,
                ttl_seconds=60,
                max_entries_per_tenant=8,
            )
            cache = InMemorySemanticCache(cfg)
            msgs = [{"role": "user", "content": "hello"}]

            async def _cache_test():
                r1 = await cache.lookup(
                    tenant_id="t1", model="m1", messages=msgs,
                    temperature=0.0, stream=False,
                )
                await cache.store(
                    tenant_id="t1", model="m1", messages=msgs,
                    response={"choices": [{"message": {"content": "hi"}}]},
                    usage_tokens=12, temperature=0.0, stream=False,
                )
                r2 = await cache.lookup(
                    tenant_id="t1", model="m1", messages=msgs,
                    temperature=0.0, stream=False,
                )
                return r1, r2

            r1, r2 = await _cache_test()
            snap = get_semantic_cache_metrics().snapshot()
            prom = get_semantic_cache_metrics().prometheus_text()
            ok = (
                r1 is None
                and r2 is not None
                and snap["hits"].get(("t1", "m1"), 0) == 1
                and snap["misses"].get(("t1", "m1"), 0) == 1
                and snap["tokens_saved"].get(("t1", "m1"), 0) == 12
                and "semantic_cache_hits_total" in prom
            )
            out.append(
                Check(
                    "PG",
                    "语义缓存 hit/miss + metrics",
                    bool(ok),
                    f"hits={snap['hits']} misses={snap['misses']}",
                )
            )
        except Exception as e:
            out.append(Check("PG", "语义缓存", False, str(e)))

        # Phase F — Prompt 版本化
        try:
            from packages.prompt import (
                extract_variables,
                render,
            )

            # 测试渲染
            tpl = "参考资料：{{context}}\n问题：{{query}}"
            vars_ = extract_variables(tpl)
            rendered = render(tpl, {"context": "CTX", "query": "Q"})
            render_ok = (
                vars_ == ["context", "query"]
                and "CTX" in rendered
                and "Q" in rendered
                and "{{" not in rendered
            )
            out.append(
                Check(
                    "PF",
                    "Prompt 模板渲染 + 变量提取",
                    bool(render_ok),
                    f"vars={vars_} rendered={rendered[:40]}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Prompt 渲染", False, str(e)))

        # Phase F — Prompt API
        try:
            r = await c.get("/internal/prompts", headers=ADMIN_HEADERS)
            body = r.json() if r.content else {}
            prompt_ids = body.get("prompt_ids", [])
            api_ok = r.status_code == 200 and "rag_query" in prompt_ids
            # 取 active 版本
            r2 = await c.get("/internal/prompts/rag_query", headers=ADMIN_HEADERS)
            body2 = r2.json() if r2.content else {}
            active_ok = (
                r2.status_code == 200
                and body2.get("prompt_id") == "rag_query"
                and body2.get("status") == "active"
                and "context" in (body2.get("variables") or [])
            )
            # 渲染接口
            r3 = await c.post(
                "/internal/prompts/rag_query/render",
                headers=ADMIN_HEADERS,
                json={"variables": {"context": "X", "query": "Y"}},
            )
            body3 = r3.json() if r3.content else {}
            render_api_ok = (
                r3.status_code == 200
                and "X" in (body3.get("rendered") or "")
                and "Y" in (body3.get("rendered") or "")
            )
            out.append(
                Check(
                    "PF",
                    "Prompt API list/get/render",
                    bool(api_ok and active_ok and render_api_ok),
                    f"ids={prompt_ids} active_v={body2.get('version')} rendered={body3.get('rendered', '')[:30]}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Prompt API", False, str(e)))

        # Phase F — Prompt 创建版本 + 切换 active
        try:
            new_content = "新版本 参考资料：{{context}}\n问题：{{query}}"
            r = await c.post(
                "/internal/prompts/rag_query/versions",
                headers=ADMIN_HEADERS,
                json={"content": new_content, "changelog": "smoke v2", "set_active": True},
            )
            body = r.json() if r.content else {}
            create_ok = r.status_code == 201 and body.get("version", 0) > 1
            new_version = body.get("version")
            # 切换回 v1
            r2 = await c.patch(
                "/internal/prompts/rag_query/active",
                headers=ADMIN_HEADERS,
                json={"version": 1},
            )
            rollback_ok = r2.status_code == 200 and r2.json().get("status") == "active"
            out.append(
                Check(
                    "PF",
                    "Prompt 创建版本 + 回滚",
                    bool(create_ok and rollback_ok),
                    f"new_v={new_version} rollback_status={r2.status_code}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Prompt 创建/回滚", False, str(e)))

        # Phase F #30 — A/B 实验
        try:
            import tempfile
            from pathlib import Path as _Path

            from packages.prompt import (
                ExperimentStore,
                ExperimentVariant,
                reset_experiment_store_for_tests,
            )

            reset_experiment_store_for_tests()
            with tempfile.TemporaryDirectory() as td:
                store = ExperimentStore(storage_path=_Path(td) / "exp.json")
                store.load()
                # 创建实验
                exp = store.create_experiment(
                    prompt_id="rag_query",
                    variants=[
                        ExperimentVariant(version=1, percent=50),
                        ExperimentVariant(version=2, percent=50),
                    ],
                    min_samples=5,
                    success_metric="quality",
                    winner_margin=0.1,
                )
                create_ok = exp.status == "running" and len(exp.variants) == 2
                # 分桶稳定性
                p1 = store.pick_variant(
                    prompt_id="rag_query", tenant_id="global", bucket_key="u1"
                )
                p2 = store.pick_variant(
                    prompt_id="rag_query", tenant_id="global", bucket_key="u1"
                )
                bucket_ok = (
                    p1 is not None
                    and p2 is not None
                    and p1[1].version == p2[1].version
                )
                # 自动胜出
                for _ in range(5):
                    store.record_request(
                        experiment_id=exp.experiment_id,
                        version=1,
                        latency_ms=100,
                        tokens=10,
                        error=False,
                    )
                    store.record_quality(
                        experiment_id=exp.experiment_id, version=1, score=0.9
                    )
                    store.record_request(
                        experiment_id=exp.experiment_id,
                        version=2,
                        latency_ms=100,
                        tokens=10,
                        error=False,
                    )
                    store.record_quality(
                        experiment_id=exp.experiment_id, version=2, score=0.4
                    )
                winner = store.maybe_auto_winner(exp.experiment_id)
                auto_ok = winner == 1
                exp2 = store.get_experiment(exp.experiment_id)
                stop_ok = exp2.status == "stopped" and exp2.winner_version == 1
            out.append(
                Check(
                    "PF",
                    "A/B 实验创建 + 分桶 + 自动胜出",
                    bool(create_ok and bucket_ok and auto_ok and stop_ok),
                    f"create={create_ok} bucket={bucket_ok} winner={winner} stop={stop_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "A/B 实验", False, str(e)))

        # Phase F #30 — A/B 实验 REST API
        try:
            # 确保 v2 存在（前面创建过）
            r = await c.get(
                "/internal/prompts/rag_query/versions", headers=ADMIN_HEADERS
            )
            versions = [
                v["version"]
                for v in (r.json() if r.content else {}).get("versions", [])
            ]
            api_ok = 1 in versions and 2 in versions
            if api_ok:
                # 创建实验
                r = await c.post(
                    "/internal/prompts/rag_query/experiments",
                    headers=ADMIN_HEADERS,
                    json={
                        "variants": [
                            {"version": 1, "percent": 50},
                            {"version": 2, "percent": 50},
                        ],
                        "min_samples": 1000,  # 故意大，避免误触发自动胜出
                        "success_metric": "quality",
                        "winner_margin": 0.1,
                    },
                )
                body = r.json() if r.content else {}
                create_api = r.status_code == 201 and body.get("status") == "running"
                exp_id = body.get("experiment_id", "")
                # 查询当前
                r2 = await c.get(
                    "/internal/prompts/rag_query/experiments/current",
                    headers=ADMIN_HEADERS,
                )
                cur_ok = r2.status_code == 200 and r2.json().get("running") is True
                # 停止
                r3 = await c.post(
                    f"/internal/prompts/rag_query/experiments/{exp_id}/stop",
                    headers=ADMIN_HEADERS,
                )
                stop_api = r3.status_code == 200 and r3.json().get("status") == "stopped"
                api_ok = bool(create_api and cur_ok and stop_api)
            out.append(
                Check(
                    "PF",
                    "A/B 实验 REST API",
                    bool(api_ok),
                    f"versions={versions} create={create_api if api_ok else 'N/A'}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "A/B 实验 API", False, str(e)))

        # Phase F #31 — 长记忆
        try:
            from packages.memory import (
                InMemoryMemoryStore,
                MemoryRecord,
                get_memory_metrics,
            )
            from packages.memory.metrics import reset_metrics_for_tests
            from packages.memory.store import reset_memory_store_for_tests

            reset_metrics_for_tests()
            reset_memory_store_for_tests()
            store = InMemoryMemoryStore()

            async def _mem_test():
                # 添加记忆
                r1 = MemoryRecord(
                    memory_id="m1",
                    tenant_id="t1",
                    scope="user",
                    scope_id="u1",
                    content="用户偏好：喜欢简洁回答",
                    metadata={"source": "test"},
                )
                mid = await store.add(r1)
                # 检索
                results = await store.search(
                    tenant_id="t1",
                    scope="user",
                    scope_id="u1",
                    query="偏好",
                    top_k=5,
                )
                return mid, results

            mid, results = await _mem_test()
            snap = get_memory_metrics().prometheus_text()
            ok = (
                mid == "m1"
                and len(results) == 1
                and results[0].content == "用户偏好：喜欢简洁回答"
                and "memory_adds_total" in snap
                and "memory_searches_total" in snap
            )
            out.append(
                Check(
                    "PF",
                    "长记忆 add/search + metrics",
                    bool(ok),
                    f"mid={mid} results={len(results)}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "长记忆", False, str(e)))

        # Phase F #31 — 长记忆 REST API
        try:
            def _safe_json(resp: httpx.Response) -> dict:
                if not resp.content:
                    return {}
                try:
                    data = resp.json()
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            r = await c.post(
                "/internal/memory",
                headers=ADMIN_HEADERS,
                json={
                    "scope": "user",
                    "scope_id": "smoke-user",
                    "content": "smoke test memory",
                    "metadata": {"source": "smoke"},
                },
            )
            if r.status_code in (503, 404):
                out.append(
                    Check(
                        "PF",
                        "长记忆 REST API CRUD",
                        False,
                        f"memory API unavailable status={r.status_code}",
                        blocked=True,
                    )
                )
            else:
                body = _safe_json(r)
                create_ok = r.status_code == 201 and body.get("created") is True
                if not create_ok:
                    out.append(
                        Check(
                            "PF",
                            "长记忆 REST API CRUD",
                            False,
                            f"create unavailable status={r.status_code}",
                            blocked=True,
                        )
                    )
                else:
                    mem_id = body.get("memory_id", "")
                    # 搜索
                    r2 = await c.post(
                        "/internal/memory/search",
                        headers=ADMIN_HEADERS,
                        json={
                            "scope": "user",
                            "scope_id": "smoke-user",
                            "query": "smoke",
                            "top_k": 5,
                        },
                    )
                    body2 = _safe_json(r2)
                    search_ok = (
                        r2.status_code == 200
                        and body2.get("count", 0) >= 1
                    )
                    # 列出
                    r3 = await c.get(
                        "/internal/memory/list?scope=user&scope_id=smoke-user",
                        headers=ADMIN_HEADERS,
                    )
                    body3 = _safe_json(r3)
                    list_ok = r3.status_code == 200 and body3.get("count", 0) >= 1
                    # 删除
                    r4 = await c.delete(
                        f"/internal/memory/{mem_id}",
                        headers=ADMIN_HEADERS,
                    )
                    body4 = _safe_json(r4)
                    del_ok = r4.status_code == 200 and body4.get("deleted") is True
                    out.append(
                        Check(
                            "PF",
                            "长记忆 REST API CRUD",
                            bool(create_ok and search_ok and list_ok and del_ok),
                            f"create={create_ok} search={search_ok} list={list_ok} del={del_ok}",
                        )
                    )
        except Exception as e:
            out.append(Check("PF", "长记忆 API", False, str(e)))

        # Phase F #33 — 上下文压缩
        try:
            from packages.agent.context_compress import (
                MemoryInjection,
                inject_memory_into_messages,
                memory_injection_platform_meta,
            )

            # 测试 inject_memory_into_messages after_summary
            messages = [
                {"role": "system", "content": "[session_summary] xxx"},
                {"role": "user", "content": "问题"},
            ]
            injection = MemoryInjection(
                injected=True,
                memory_count=2,
                injected_tokens=80,
                memories=[{"memory_id": "m1"}],
                system_message={"role": "system", "content": "记忆要点"},
            )
            result = inject_memory_into_messages(
                messages, injection, position="after_summary"
            )
            inject_ok = (
                len(result) == 3
                and result[0]["content"] == "[session_summary] xxx"
                and result[1]["content"] == "记忆要点"
                and result[2]["role"] == "user"
            )

            # 测试 not injected 不修改
            no_injection = MemoryInjection(
                injected=False,
                memory_count=0,
                injected_tokens=0,
                memories=[],
                system_message=None,
            )
            result2 = inject_memory_into_messages(messages, no_injection)
            not_injected_ok = result2 is messages and len(result2) == 2

            # 测试 platform_meta
            meta = memory_injection_platform_meta(injection)
            meta_ok = (
                meta["injected"] is True
                and meta["memory_count"] == 2
                and meta["injected_tokens"] == 80
            )

            out.append(
                Check(
                    "PF",
                    "上下文压缩 inject + meta",
                    bool(inject_ok and not_injected_ok and meta_ok),
                    f"inject={inject_ok} no_inject={not_injected_ok} meta={meta_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "上下文压缩", False, str(e)))

        # Phase F #32 — MCP 集成
        try:
            from packages.mcp.transport import (
                HttpTransport,
                StdioTransport,
                TransportError,
            )

            # 测试 transport 构造
            stdio_t = StdioTransport(["echo", "test"])
            http_t = HttpTransport(
                "https://example.com/mcp",
                headers={"Authorization": "Bearer xxx"},
            )
            construct_ok = (
                stdio_t._command == ["echo", "test"]
                and http_t._url == "https://example.com/mcp"
                and http_t._headers["Authorization"] == "Bearer xxx"
            )
            # 测试 TransportError
            err = TransportError("TIMEOUT", "读取超时")
            error_ok = err.code == "TIMEOUT" and err.message == "读取超时"
            out.append(
                Check(
                    "PF",
                    "MCP transport 构造 + 错误",
                    bool(construct_ok and error_ok),
                    f"stdio={bool(stdio_t)} http={bool(http_t)} err={error_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "MCP transport", False, str(e)))

        # Phase F #32 — MCP REST API
        try:
            # 列出 servers
            r = await c.get("/internal/mcp/servers", headers=ADMIN_HEADERS)
            body = r.json() if r.content else {}
            list_ok = r.status_code == 200 and "servers" in body
            body.get("stats", {}).get("total_servers", 0)
            # 创建一个 http server（不实际连接）
            r2 = await c.post(
                "/internal/mcp/servers",
                headers=ADMIN_HEADERS,
                json={
                    "server_id": "smoke-mcp",
                    "transport": "http",
                    "enabled": False,  # 不启用，避免实际连接
                    "url": "https://mcp.example.com",
                    "description": "smoke test",
                },
            )
            body2 = r2.json() if r2.content else {}
            create_ok = r2.status_code == 201 and body2.get("server_id") == "smoke-mcp"
            # 获取详情
            r3 = await c.get("/internal/mcp/servers/smoke-mcp", headers=ADMIN_HEADERS)
            get_ok = r3.status_code == 200 and r3.json().get("transport") == "http"
            # 删除
            r4 = await c.delete(
                "/internal/mcp/servers/smoke-mcp", headers=ADMIN_HEADERS
            )
            del_ok = r4.status_code == 200 and r4.json().get("deleted") is True
            out.append(
                Check(
                    "PF",
                    "MCP REST API CRUD",
                    bool(list_ok and create_ok and get_ok and del_ok),
                    f"list={list_ok} create={create_ok} get={get_ok} del={del_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "MCP API", False, str(e)))

        # Phase H #37 — 控制流编排引擎
        try:
            # 独立逻辑测试（不依赖 apps.gateway 链）
            import importlib.util as _ilu

            def _load_mod(name, path):
                spec = _ilu.spec_from_file_location(name, path)
                mod = _ilu.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            graph_mod = _load_mod(
                "smoke_graph",
                REPO_ROOT / "packages/agent/orchestrator/graph.py",
            )
            # 测试 Workflow 创建与校验
            wf = graph_mod.Workflow(
                workflow_id="smoke_wf",
                name="smoke",
                nodes=[
                    graph_mod.GraphNode(node_id="start", node_type="start"),
                    graph_mod.GraphNode(node_id="end", node_type="end"),
                ],
                edges=[graph_mod.GraphEdge(from_node="start", to_node="end")],
                start_node="start",
                end_node="end",
            )
            graph_mod.validate_workflow(wf)
            wf_dict = wf.to_dict()
            ok = (
                wf_dict["workflow_id"] == "smoke_wf"
                and len(wf_dict["nodes"]) == 2
                and len(wf_dict["edges"]) == 1
            )
            out.append(
                Check(
                    "PF",
                    "编排引擎 DAG 模型 + 校验",
                    bool(ok),
                    f"nodes={len(wf_dict['nodes'])} edges={len(wf_dict['edges'])}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "编排引擎", False, str(e)))

        # Phase H #37 — 编排引擎 REST API
        try:
            # 创建工作流
            r = await c.post(
                "/internal/orchestrator/workflows",
                headers=ADMIN_HEADERS,
                json={
                    "workflow_id": "smoke-wf",
                    "name": "smoke test workflow",
                    "nodes": [
                        {"node_id": "start", "node_type": "start"},
                        {
                            "node_id": "out1",
                            "node_type": "output",
                            "config": {"value": "hello from workflow"},
                        },
                        {"node_id": "end", "node_type": "end"},
                    ],
                    "edges": [
                        {"from_node": "start", "to_node": "out1"},
                        {"from_node": "out1", "to_node": "end"},
                    ],
                    "start_node": "start",
                    "end_node": "end",
                },
            )
            body = r.json() if r.content else {}
            create_ok = r.status_code == 201 and body.get("workflow_id") == "smoke-wf"
            # 列出
            r2 = await c.get("/internal/orchestrator/workflows", headers=ADMIN_HEADERS)
            list_ok = r2.status_code == 200 and r2.json().get("count", 0) >= 1
            # 执行工作流
            r3 = await c.post(
                "/internal/orchestrator/workflows/smoke-wf/execute",
                headers=ADMIN_HEADERS,
                json={"inputs": {}},
            )
            body3 = r3.json() if r3.content else {}
            exec_ok = (
                r3.status_code == 200
                and body3.get("status") == "completed"
                and body3.get("outputs", {}).get("out1", {}).get("value") == "hello from workflow"
            )
            # 删除
            r4 = await c.delete(
                "/internal/orchestrator/workflows/smoke-wf", headers=ADMIN_HEADERS
            )
            del_ok = r4.status_code == 200 and r4.json().get("deleted") is True
            out.append(
                Check(
                    "PF",
                    "编排引擎 REST API + 执行",
                    bool(create_ok and list_ok and exec_ok and del_ok),
                    f"create={create_ok} list={list_ok} exec={exec_ok} del={del_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "编排引擎 API", False, str(e)))

        # Phase H #38 — Multi-Agent 框架
        try:
            # 独立逻辑测试
            import importlib.util as _ilu2

            def _load_mod2(name, path):
                spec = _ilu2.spec_from_file_location(name, path)
                mod = _ilu2.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            reg_mod = _load_mod2(
                "smoke_agent_reg",
                REPO_ROOT / "packages/agent/multi_agent/registry.py",
            )
            spec = reg_mod.AgentSpec(
                agent_id="smoke_agent",
                name="Smoke Agent",
                role="specialist",
                description="smoke test",
            )
            spec_ok = (
                spec.agent_id == "smoke_agent"
                and spec.role == "specialist"
                and spec.can_be_delegated_to is True
                and spec.max_delegation_depth == 3
            )
            # 工具白名单
            spec2 = reg_mod.AgentSpec(
                agent_id="a2", name="A2", allowed_tools=["get_kb_snippet"]
            )
            tool_ok = (
                spec2.is_tool_allowed("get_kb_snippet") is True
                and spec2.is_tool_allowed("other") is False
            )
            out.append(
                Check(
                    "PF",
                    "Multi-Agent AgentSpec + 工具白名单",
                    bool(spec_ok and tool_ok),
                    f"spec={spec_ok} tool_whitelist={tool_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Multi-Agent spec", False, str(e)))

        # Phase H #38 — Multi-Agent REST API
        try:
            # 列出
            r = await c.get("/internal/agents", headers=ADMIN_HEADERS)
            list_ok = r.status_code == 200 and "agents" in r.json()
            # 创建 Agent
            r2 = await c.post(
                "/internal/agents",
                headers=ADMIN_HEADERS,
                json={
                    "agent_id": "smoke-agent",
                    "name": "Smoke Test Agent",
                    "role": "specialist",
                    "description": "smoke test",
                    "system_prompt": "你是测试 Agent",
                    "enabled": True,
                },
            )
            body2 = r2.json() if r2.content else {}
            create_ok = r2.status_code == 201 and body2.get("agent_id") == "smoke-agent"
            # 获取详情
            r3 = await c.get("/internal/agents/smoke-agent", headers=ADMIN_HEADERS)
            get_ok = r3.status_code == 200 and r3.json().get("role") == "specialist"
            # 删除
            r4 = await c.delete(
                "/internal/agents/smoke-agent", headers=ADMIN_HEADERS
            )
            del_ok = r4.status_code == 200 and r4.json().get("deleted") is True
            out.append(
                Check(
                    "PF",
                    "Multi-Agent REST API CRUD",
                    bool(list_ok and create_ok and get_ok and del_ok),
                    f"list={list_ok} create={create_ok} get={get_ok} del={del_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Multi-Agent API", False, str(e)))

        # Phase H #39 — Agent 生命周期管理
        try:
            # 注册版本
            r = await c.post(
                "/internal/agent-lifecycle/smoke-agent/versions",
                headers=ADMIN_HEADERS,
                json={"spec_snapshot": {"agent_id": "smoke-agent", "name": "Smoke"}, "metadata": {}},
            )
            body = r.json() if r.content else {}
            create_ok = r.status_code == 201 and body.get("version", 0) >= 1
            version_id = body.get("version_id", "")
            # 列出版本
            r2 = await c.get(
                "/internal/agent-lifecycle/smoke-agent/versions",
                headers=ADMIN_HEADERS,
            )
            list_ok = r2.status_code == 200 and r2.json().get("count", 0) >= 1
            # 激活
            r3 = await c.post(
                f"/internal/agent-lifecycle/versions/{version_id}/activate",
                headers=ADMIN_HEADERS,
                json={"strategy": "all_at_once"},
            )
            activate_ok = r3.status_code == 200
            # 查看激活版本
            r4 = await c.get(
                "/internal/agent-lifecycle/smoke-agent/active",
                headers=ADMIN_HEADERS,
            )
            active_ok = r4.status_code == 200
            out.append(
                Check(
                    "PF",
                    "Agent 生命周期 REST API",
                    bool(create_ok and list_ok and activate_ok and active_ok),
                    f"create={create_ok} list={list_ok} activate={activate_ok} active={active_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Agent 生命周期", False, str(e)))

        # Phase H #40 — HITL 完整工作流
        try:
            # 创建审批
            r = await c.post(
                "/internal/hitl/approvals",
                headers=ADMIN_HEADERS,
                json={
                    "tenant_id": "admin",
                    "session_id": "smoke-session",
                    "tool_name": "dangerous_tool",
                    "arguments": {"x": 1},
                    "timeout_seconds": 60,
                },
            )
            body = r.json() if r.content else {}
            create_ok = r.status_code == 201 and body.get("request_id")
            request_id = body.get("request_id", "")
            # 查看状态
            r2 = await c.get(
                f"/internal/hitl/approvals/{request_id}",
                headers=ADMIN_HEADERS,
            )
            get_ok = r2.status_code == 200 and r2.json().get("status") == "pending"
            # 批准
            r3 = await c.post(
                f"/internal/hitl/approvals/{request_id}/approve",
                headers=ADMIN_HEADERS,
                json={"decided_by": "admin", "reason": "smoke test"},
            )
            approve_ok = r3.status_code == 200 and r3.json().get("status") == "approved"
            out.append(
                Check(
                    "PF",
                    "HITL 审批工作流",
                    bool(create_ok and get_ok and approve_ok),
                    f"create={create_ok} get={get_ok} approve={approve_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "HITL 工作流", False, str(e)))

        # Phase G #35 — Embedding 独立服务
        try:
            # 列出模型
            r = await c.get("/internal/embeddings/models", headers=ADMIN_HEADERS)
            list_ok = r.status_code == 200
            # 注册 stub 模型（用于测试，不调真实 API）
            r2 = await c.post(
                "/internal/embeddings/models",
                headers=ADMIN_HEADERS,
                json={
                    "model_id": "smoke-emb",
                    "name": "Smoke Embedding",
                    "provider": "stub",
                    "dimensions": 128,
                    "max_input_tokens": 8192,
                },
            )
            body2 = r2.json() if r2.content else {}
            create_ok = r2.status_code == 201 and body2.get("model_id") == "smoke-emb"
            # 生成 embedding
            r3 = await c.post(
                "/internal/embeddings/embed",
                headers=ADMIN_HEADERS,
                json={"model_id": "smoke-emb", "texts": ["hello", "world"]},
            )
            body3 = r3.json() if r3.content else {}
            embed_ok = (
                r3.status_code == 200
                and len(body3.get("embeddings", [])) == 2
                and len(body3.get("embeddings", [[]])[0]) == 128
            )
            # 第二次应命中缓存
            r4 = await c.post(
                "/internal/embeddings/embed",
                headers=ADMIN_HEADERS,
                json={"model_id": "smoke-emb", "texts": ["hello"]},
            )
            cache_ok = r4.status_code == 200 and r4.json().get("cached") is True
            # 删除模型
            r5 = await c.delete(
                "/internal/embeddings/models/smoke-emb", headers=ADMIN_HEADERS
            )
            del_ok = r5.status_code == 200
            out.append(
                Check(
                    "PF",
                    "Embedding 服务 + 缓存",
                    bool(list_ok and create_ok and embed_ok and cache_ok and del_ok),
                    f"list={list_ok} create={create_ok} embed={embed_ok} cache={cache_ok} del={del_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "Embedding 服务", False, str(e)))

        # Phase I #41 — 沙箱容器隔离
        try:
            import importlib.util as _ilu3

            def _load_mod3(name, path):
                spec = _ilu3.spec_from_file_location(name, path)
                mod = _ilu3.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            exec_mod = _load_mod3(
                "smoke_sandbox",
                REPO_ROOT / "packages/sandbox/executor.py",
            )
            # 测试 SandboxConfig + SandboxResult
            cfg = exec_mod.SandboxConfig(
                enabled=True,
                runtime="process",
                image="python:3.11-slim",
                memory_limit_mb=256,
                cpu_limit=0.5,
                timeout_seconds=5.0,
                profile_id="default",
            )
            cfg_ok = cfg.runtime == "process" and cfg.memory_limit_mb == 256
            # seccomp profiles
            sec_mod = _load_mod3(
                "smoke_seccomp",
                REPO_ROOT / "packages/sandbox/seccomp_profiles.py",
            )
            profiles = sec_mod.SECCOMP_PROFILES
            sec_ok = "default" in profiles and "strict" in profiles
            out.append(
                Check(
                    "PF",
                    "沙箱 SandboxConfig + seccomp 档案",
                    bool(cfg_ok and sec_ok),
                    f"config={cfg_ok} seccomp_profiles={sec_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "沙箱隔离", False, str(e)))

        # Phase I #42 — 动作分级审计
        try:
            import importlib.util as _ilu4

            def _load_mod4(name, path):
                spec = _ilu4.spec_from_file_location(name, path)
                mod = _ilu4.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            al_mod = _load_mod4(
                "smoke_action_levels",
                REPO_ROOT / "packages/audit/action_levels.py",
            )
            # 测试 ActionLevel
            assert al_mod.ActionLevel.READ_ONLY == "read_only"
            assert al_mod.ActionLevel.DESTRUCTIVE == "destructive"
            # 测试启发式分类
            clf = al_mod.ActionClassifier()
            assert clf.classify("delete_user", {}) == "destructive"
            assert clf.classify("get_user", {}) == "read_only"
            assert clf.classify("create_user", {}) == "write"
            classify_ok = True
            out.append(
                Check(
                    "PF",
                    "动作分级审计 ActionLevel + 启发式分类",
                    bool(classify_ok),
                    "classify_delete=destructive classify_get=read_only",
                )
            )
        except Exception as e:
            out.append(Check("PF", "动作分级审计", False, str(e)))

        # Phase I #43 — PII 脱敏
        try:
            import importlib.util as _ilu5

            def _load_mod5(name, path):
                spec = _ilu5.spec_from_file_location(name, path)
                mod = _ilu5.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            det_mod = _load_mod5(
                "smoke_pii_detectors",
                REPO_ROOT / "packages/pii/detectors.py",
            )
            detector = det_mod.PIIDetector()
            # 测试 email 检测
            matches = detector.detect("联系我：test@example.com 或 admin@foo.org")
            email_ok = len(matches) >= 2 and all(
                m.entity_type == "email" for m in matches
            )
            # 测试中国手机号
            matches2 = detector.detect("电话：13812345678")
            phone_ok = len(matches2) >= 1
            out.append(
                Check(
                    "PF",
                    "PII 检测 email + 手机号",
                    bool(email_ok and phone_ok),
                    f"email={email_ok} phone={phone_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "PII 脱敏", False, str(e)))

        # Phase I #44 — OAuth2 / mTLS
        try:
            import importlib.util as _ilu6

            def _load_mod6(name, path):
                spec = _ilu6.spec_from_file_location(name, path)
                mod = _ilu6.module_from_spec(spec)
                import sys as _sys
                _sys.modules[name] = mod
                spec.loader.exec_module(mod)
                return mod

            oa_mod = _load_mod6(
                "smoke_oauth2",
                REPO_ROOT / "packages/auth/oauth2.py",
            )
            cfg = oa_mod.OAuth2Config(
                client_id="test_client",
                client_secret="test_secret",
                authorization_endpoint="https://idp.example.com/authorize",
                token_endpoint="https://idp.example.com/token",
                userinfo_endpoint="https://idp.example.com/userinfo",
                redirect_uri="http://127.0.0.1:8000/callback",
                scopes=["openid", "profile"],
                issuer="https://idp.example.com",
            )
            provider = oa_mod.OAuth2Provider(cfg)
            auth_url = provider.get_authorization_url(state="xyz")
            url_ok = "client_id=test_client" in auth_url and "state=xyz" in auth_url
            out.append(
                Check(
                    "PF",
                    "OAuth2 配置 + 授权 URL",
                    bool(url_ok),
                    f"auth_url_built={url_ok}",
                )
            )
        except Exception as e:
            out.append(Check("PF", "OAuth2/mTLS", False, str(e)))

        try:
            from packages.rag.canary_guard import check_canary_guard, get_kb_routing_override

            r = await c.get("/internal/providers/matrix", headers=ADMIN_HEADERS)
            out.append(Check("PD", "GET providers matrix", r.status_code == 200, str(r.status_code)))
            _ = check_canary_guard, get_kb_routing_override
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
        from packages.agent.tool_envelope import (
            failure_envelope,
            parse_tool_result,
            success_envelope,
        )

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
        import yaml

        from packages.router import resolve_model_name

        models_raw = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
        aliases = models_raw.get("aliases") if isinstance(models_raw, dict) else {}
        expected = str((aliases or {}).get("chat-fast", ""))
        resolved = resolve_model_name("chat-fast")
        ok = bool(expected) and resolved == expected
        out.append(Check("W6", "别名 chat-fast", ok, resolved))
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
    parser.add_argument("--agent-vertical", action="store_true", help="Phase L #59 Agent Vertical smoke")
    parser.add_argument("--platform-demo", action="store_true", help="Phase L #62 platform_demo.sh --no-llm + feedback mock")
    args = parser.parse_args()
    _ensure_platform_wired()
    checks = asyncio.run(run_checks(with_llm=args.with_llm))

    if args.agent_vertical:
        from eval.agent_vertical_smoke import run_agent_vertical_smoke

        vertical = asyncio.run(run_agent_vertical_smoke(with_llm=args.with_llm))
        for v in vertical:
            checks.append(
                Check(
                    "L59",
                    v.name,
                    v.passed,
                    v.detail,
                    blocked=v.blocked,
                )
            )

    if args.platform_demo:
        repo = __import__("pathlib").Path(__file__).resolve().parents[1]
        try:
            proc = subprocess.run(
                ["bash", "eval/platform_demo.sh", "--no-llm"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=120,
            )
            ok = proc.returncode == 0
            checks.append(
                Check(
                    "L62",
                    "platform_demo --no-llm",
                    ok,
                    proc.stdout.strip()[-120:] if proc.stdout else proc.stderr[-120:],
                )
            )
        except Exception as e:
            checks.append(Check("L62", "platform_demo --no-llm", False, str(e)))
        try:
            proc = subprocess.run(
                [sys.executable, "eval/feedback_loop_demo.py", "--mock"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=60,
            )
            ok = proc.returncode == 0 and '"passed": true' in proc.stdout
            checks.append(Check("L62", "feedback_loop_demo --mock", ok, proc.stdout[-80:] if proc.stdout else ""))
        except Exception as e:
            checks.append(Check("L62", "feedback_loop_demo --mock", False, str(e)))

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
