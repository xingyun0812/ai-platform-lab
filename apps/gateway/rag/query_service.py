from __future__ import annotations

import logging
import time
from typing import Any

from apps.gateway.llm_proxy import forward_chat_completions
from apps.gateway.quota import DailyQuotaTracker
from apps.gateway.rag.pipeline import resolve_retrieve_version
from apps.gateway.settings import get_settings
from packages.contracts.rag_schemas import RagCitation, RagQueryTimings, RetrievedChunk
from packages.observability.context import get_trace_id
from packages.rag.prompt import build_context_block, load_prompt_template, render_rag_prompt
from packages.rag.retrieval import retrieve_chunks

logger = logging.getLogger("ai_platform.rag.query")


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
) -> dict[str, Any]:
    settings = get_settings()
    effective_min = min_score if min_score is not None else settings.rag_min_score
    effective_model = model or settings.rag_query_model or settings.default_model

    t0 = time.perf_counter()
    retrieve_start = t0
    try:
        resolved_version, raw_chunks = await retrieve_chunks(
            kb_id=kb_id,
            version=version,
            query=query,
            top_k=top_k,
            resolve_version=resolve_retrieve_version,
        )
    except ValueError as e:
        raise RagQueryRefusal("RAG_KB_NOT_FOUND", str(e)) from e
    retrieve_ms = (time.perf_counter() - retrieve_start) * 1000

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

    if not quota_tracker.try_consume(tenant_id, daily_request_quota):
        raise RagQueryRefusal(
            "QUOTA_EXCEEDED",
            "租户日配额已用尽",
            detail={"tenant_id": tenant_id, "quota": daily_request_quota},
        )

    template = load_prompt_template(settings.rag_prompt_path)
    context = build_context_block(chunks)
    user_prompt = render_rag_prompt(template, context=context, query=query)

    llm_start = time.perf_counter()
    payload = {
        "model": effective_model,
        "messages": [
            {
                "role": "system",
                "content": "你是企业知识库问答助手，严格依据用户消息中的参考资料作答。",
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    status, upstream_json, err = await forward_chat_completions(payload)
    llm_ms = (time.perf_counter() - llm_start) * 1000

    if err and upstream_json is None:
        raise RuntimeError(err)
    if upstream_json is None or not (200 <= status < 300):
        raise RuntimeError(f"LLM upstream status {status}: {err or upstream_json}")

    answer = extract_answer(upstream_json)
    total_ms = (time.perf_counter() - t0) * 1000

    timings = RagQueryTimings(
        retrieve_ms=round(retrieve_ms, 2),
        llm_ms=round(llm_ms, 2),
        total_ms=round(total_ms, 2),
    )

    logger.info(
        "rag_query",
        extra={
            "trace_id": get_trace_id(),
            "kb_id": kb_id,
            "version": resolved_version,
            "retrieve_ms": timings.retrieve_ms,
            "llm_ms": timings.llm_ms,
            "total_ms": timings.total_ms,
            "chunks_used": len(chunks),
        },
    )

    return {
        "kb_id": kb_id,
        "version": resolved_version,
        "query": query,
        "answer": answer,
        "citations": to_citations(chunks),
        "timings": timings,
        "model": effective_model,
        "min_score": effective_min,
        "trace_id": get_trace_id(),
    }
