from __future__ import annotations

import logging
import time
from typing import Any

from apps.gateway.llm_semantic_cache import lookup_llm_completion, store_llm_completion
from apps.gateway.quota import DailyQuotaTracker
from apps.gateway.rag.pipeline import resolve_query_version
from apps.gateway.settings import get_settings
from packages.billing.budget import budget_platform_meta, get_budget_snapshot
from packages.billing.recorder import record_upstream_usage
from packages.contracts.rag_schemas import RagCitation, RagQueryTimings, RetrievedChunk
from packages.observability.context import get_trace_id
from packages.rag.prompt import build_context_block, load_prompt_template, render_rag_prompt
from packages.rag.rerank import rerank_chunks, rerank_provider_name
from packages.rag.rerank_providers import provider_config_from_settings
from packages.rag.retrieval import retrieve_chunks
from packages.router.model_router import forward_with_model_router, resolve_model_name

logger = logging.getLogger("ai_platform.rag.query")


def _resolve_rag_prompt_template(settings, *, bucket_key: str | None = None) -> tuple[str, dict[str, Any]]:
    """Phase F：优先从 prompt registry 取 rag_query 模板；否则回退 legacy txt。

    若启用 A/B 实验 + bucket_key，则按实验分桶取版本；返回 (template, exp_info)。
    registry 中模板用 {{context}}/{{query}} 语法，但 render_rag_prompt 期望 {context}/{query}。
    因此：若模板含 {{var}} 双花括号，先渲染为 {context}/{query} 占位文本。
    """
    exp_info: dict[str, Any] = {}
    if settings.prompt_registry_enabled:
        from packages.prompt import get_experiment_store, get_registry

        reg = get_registry()
        if reg is not None:
            try:
                if bucket_key and settings.prompt_experiment_enabled:
                    store = get_experiment_store()
                    if store is not None:
                        # render_with_experiment 会自动按 bucket 分桶或回退 active
                        content, _entry, exp_info = reg.render_with_experiment(
                            "rag_query",
                            {"context": "{context}", "query": "{query}"},
                            bucket_key=bucket_key,
                            experiment_store=store,
                        )
                        # 用 {{context}} → {context} 留给 render_rag_prompt
                        return content.replace("{{context}}", "{context}").replace(
                            "{{query}}", "{query}"
                        ), exp_info
                # 无实验：取 active
                entry = reg.get_active("rag_query")
                if entry is not None and entry.version > 0:
                    return (
                        entry.content.replace("{{context}}", "{context}").replace(
                            "{{query}}", "{query}"
                        ),
                        exp_info,
                    )
            except Exception as e:
                logger.warning("prompt registry rag_query lookup failed: %s", e)
    return load_prompt_template(settings.rag_prompt_path), exp_info


def _resolve_rag_system_prompt(settings) -> str:
    """Phase F：优先从 registry 取 rag_system；否则回退硬编码。"""
    if settings.prompt_registry_enabled:
        from packages.prompt import get_registry

        reg = get_registry()
        if reg is not None:
            try:
                entry = reg.get_active("rag_system")
                if entry is not None and entry.version > 0:
                    return entry.content
            except Exception as e:
                logger.warning("prompt registry rag_system lookup failed: %s", e)
    return "你是企业知识库问答助手，严格依据用户消息中的参考资料作答。"


class RagQueryRefusal(Exception):
    """业务拒答：携带业务错误码，与 HTTP 状态在路由层映射。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


def filter_chunks_by_score(
    chunks: list[RetrievedChunk],
    min_score: float,
) -> list[RetrievedChunk]:
    return [c for c in chunks if c.score >= min_score]


def to_citations(chunks: list[RetrievedChunk]) -> list[RagCitation]:
    return [
        RagCitation(
            chunk_id=c.chunk_id,
            kb_id=c.kb_id,
            version=c.version,
            source_uri=c.source_uri,
            score=c.score,
        )
        for c in chunks
    ]


def extract_answer(upstream_json: dict[str, Any]) -> str:
    choices = upstream_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("upstream 响应缺少 choices")
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg, dict):
        raise RuntimeError("upstream choices[0].message 格式错误")
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("upstream 返回空 content")
    return content.strip()


async def run_rag_query(
    *,
    kb_id: str,
    version: int | None,
    query: str,
    top_k: int,
    min_score: float | None,
    model: str | None,
    tenant_id: str,
    daily_request_quota: int,
    quota_tracker: DailyQuotaTracker,
    token_budget_daily: int = -1,
    token_budget_monthly: int = -1,
) -> dict[str, Any]:
    settings = get_settings()
    effective_min = min_score if min_score is not None else settings.rag_min_score
    effective_model = resolve_model_name(model or settings.rag_query_model)

    t0 = time.perf_counter()
    retrieve_start = t0
    route_label = "pinned" if version is not None else "stable"
    routing_bucket = 0

    def _resolve_version(kb: str, ver: int | None) -> int:
        nonlocal route_label, routing_bucket
        resolved, route_label, routing_bucket = resolve_query_version(
            kb,
            ver,
            tenant_id=tenant_id,
            query=query,
        )
        return resolved

    try:
        resolved_version, raw_chunks, retrieve_breakdown = await retrieve_chunks(
            kb_id=kb_id,
            version=version,
            query=query,
            top_k=top_k,
            resolve_version=_resolve_version,
        )
    except ValueError as e:
        raise RagQueryRefusal("RAG_KB_NOT_FOUND", str(e)) from e
    retrieve_ms = (time.perf_counter() - retrieve_start) * 1000

    rerank_ms = 0.0
    rerank_cfg = provider_config_from_settings(settings)
    if settings.rag_rerank_enabled and raw_chunks:
        raw_chunks, rerank_ms = rerank_chunks(
            query,
            raw_chunks,
            top_n=settings.rag_rerank_top_n,
            mode=settings.rag_rerank_mode,
            provider_config=rerank_cfg,
        )

    if not raw_chunks:
        raise RagQueryRefusal(
            "RAG_NO_EVIDENCE",
            "检索未命中任何片段，拒答",
            detail={"kb_id": kb_id, "version": resolved_version, "top_k": top_k},
        )

    chunks = filter_chunks_by_score(raw_chunks, effective_min)
    if not chunks:
        max_score = max(c.score for c in raw_chunks)
        raise RagQueryRefusal(
            "RAG_LOW_CONFIDENCE",
            f"最高相关分 {max_score:.4f} 低于阈值 {effective_min}",
            detail={
                "kb_id": kb_id,
                "version": resolved_version,
                "min_score": effective_min,
                "max_score": max_score,
                "candidates": len(raw_chunks),
            },
        )

    from packages.billing.budget import is_budget_exceeded

    exceeded, code, detail = is_budget_exceeded(
        tenant_id,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
    if exceeded:
        raise RagQueryRefusal(
            code or "BUDGET_EXCEEDED",
            "租户 token 预算已用尽",
            detail={**(detail or {}), "tenant_id": tenant_id},
        )

    if not quota_tracker.try_consume(tenant_id, daily_request_quota):
        raise RagQueryRefusal(
            "QUOTA_EXCEEDED",
            "租户日配额已用尽",
            detail={"tenant_id": tenant_id, "quota": daily_request_quota},
        )

    template, exp_info = _resolve_rag_prompt_template(
        settings, bucket_key=f"{tenant_id}|{query}"
    )
    context = build_context_block(chunks)
    user_prompt = render_rag_prompt(template, context=context, query=query)

    llm_start = time.perf_counter()
    system_content = _resolve_rag_system_prompt(settings)
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]
    rag_temperature = 0.2
    cached_body = await lookup_llm_completion(
        tenant_id=tenant_id,
        model=effective_model,
        messages=messages,
        temperature=rag_temperature,
    )
    cache_hit = cached_body is not None
    if cache_hit:
        upstream_body = cached_body
        model_used = effective_model
        usage = None
    else:
        payload = {
            "model": effective_model,
            "messages": messages,
            "temperature": rag_temperature,
        }
        routed = await forward_with_model_router(
            payload,
            requested_model=model or settings.rag_query_model,
        )
        if routed.error and routed.body is None:
            raise RuntimeError(routed.error)
        if routed.body is None or not (200 <= routed.status < 300):
            raise RuntimeError(f"LLM upstream status {routed.status}: {routed.error or routed.body}")
        upstream_body = routed.body
        model_used = routed.model_used or effective_model
        usage = record_upstream_usage(
            tenant_id=tenant_id,
            path="/v1/rag/query",
            model=model_used,
            upstream_body=upstream_body,
            trace_id=get_trace_id(),
        )
        await store_llm_completion(
            tenant_id=tenant_id,
            model=effective_model,
            messages=messages,
            response=dict(upstream_body),
            usage_tokens=(usage.total_tokens if usage else 0),
            temperature=rag_temperature,
        )
    llm_ms = (time.perf_counter() - llm_start) * 1000

    answer = extract_answer(upstream_body)
    # Phase F #30：若命中 A/B 实验，记录指标 + 触发自动胜出
    if exp_info.get("experiment_id") and exp_info.get("variant_version") is not None:
        try:
            from packages.prompt import get_experiment_store

            store = get_experiment_store()
            if store is not None:
                llm_ms_for_metrics = (time.perf_counter() - llm_start) * 1000
                tokens = usage.total_tokens if usage else 0
                store.record_request(
                    experiment_id=exp_info["experiment_id"],
                    version=exp_info["variant_version"],
                    latency_ms=llm_ms_for_metrics,
                    tokens=tokens,
                    error=False,
                )
                store.maybe_auto_winner(exp_info["experiment_id"])
        except Exception as e:
            logger.warning("experiment metrics record failed: %s", e)
    snap = get_budget_snapshot(
        tenant_id,
        token_budget_daily=token_budget_daily,
        token_budget_monthly=token_budget_monthly,
    )
    total_ms = (time.perf_counter() - t0) * 1000

    timings = RagQueryTimings(
        retrieve_ms=round(retrieve_ms, 2),
        llm_ms=round(llm_ms, 2),
        total_ms=round(total_ms, 2),
        retrieve_vector_ms=round(retrieve_breakdown.vector_ms, 2) if retrieve_breakdown else None,
        retrieve_bm25_ms=round(retrieve_breakdown.bm25_ms, 2) if retrieve_breakdown else None,
        fusion_ms=round(retrieve_breakdown.fusion_ms, 2) if retrieve_breakdown else None,
        rerank_ms=round(rerank_ms, 2) if settings.rag_rerank_enabled else None,
    )

    logger.info(
        "rag_query",
        extra={
            "trace_id": get_trace_id(),
            "kb_id": kb_id,
            "version": resolved_version,
            "route": route_label,
            "routing_bucket": routing_bucket,
            "retrieve_ms": timings.retrieve_ms,
            "rerank_ms": rerank_ms,
            "llm_ms": timings.llm_ms,
            "total_ms": timings.total_ms,
            "chunks_used": len(chunks),
        },
    )

    result: dict[str, Any] = {
        "kb_id": kb_id,
        "version": resolved_version,
        "query": query,
        "answer": answer,
        "citations": to_citations(chunks),
        "timings": timings,
        "model": model_used,
        "min_score": effective_min,
        "trace_id": get_trace_id(),
    }
    platform_meta: dict[str, Any] = {
        "routing": {
            "route": route_label,
            "bucket": routing_bucket,
            "version": resolved_version,
        },
    }
    if exp_info.get("experiment_id"):
        platform_meta["experiment"] = exp_info
    if cache_hit:
        platform_meta["cache_hit"] = True
        cached_platform = (cached_body or {}).get("_platform")
        if isinstance(cached_platform, dict):
            for key in ("cache_mode", "cache_similarity", "cache_age_seconds"):
                if key in cached_platform:
                    platform_meta[key] = cached_platform[key]
    if settings.rag_rerank_enabled:
        platform_meta["rerank"] = {
            "provider": rerank_provider_name(settings.rag_rerank_mode, rerank_cfg),
            "mode": settings.rag_rerank_mode,
        }
    if usage is not None:
        platform_meta["usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            **budget_platform_meta(snap, usage.total_tokens),
        }
    result["_platform"] = platform_meta
    return result
