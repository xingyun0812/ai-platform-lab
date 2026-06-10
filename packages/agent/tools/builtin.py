from __future__ import annotations

import ast
import operator
from typing import Any

import httpx

from apps.gateway.rag.pipeline import resolve_retrieve_version
from packages.agent.tool_envelope import success_envelope
from packages.rag.retrieval import retrieve_chunks

_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


def _safe_eval_expr(expr: str) -> float:
    node = ast.parse(expr.strip(), mode="eval")

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _eval(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return _BIN_OPS[ast.USub](_eval(n.operand))
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            return _BIN_OPS[type(n.op)](_eval(n.left), _eval(n.right))
        raise ValueError(f"不支持的表达式: {expr}")

    return _eval(node)


async def handle_calc(arguments: dict[str, Any]) -> str:
    expr = arguments.get("expression")
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("缺少 expression")
    result = _safe_eval_expr(expr)
    return success_envelope({"expression": expr, "result": result}, quality_score=1.0)


async def handle_get_kb_snippet(arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("缺少 query")
    kb_id = arguments.get("kb_id")
    if not isinstance(kb_id, str) or not kb_id.strip():
        raise ValueError("缺少 kb_id")
    version = arguments.get("version")
    ver: int | None = int(version) if version is not None else None
    top_k = arguments.get("top_k", 3)
    if not isinstance(top_k, int) or top_k < 1:
        top_k = 3

    _, chunks = await retrieve_chunks(
        kb_id=kb_id.strip(),
        version=ver,
        query=query.strip(),
        top_k=min(top_k, 10),
        resolve_version=resolve_retrieve_version,
    )
    snippets = [
        {
            "chunk_id": c.chunk_id,
            "score": c.score,
            "text": c.text[:500],
            "source_uri": c.source_uri,
        }
        for c in chunks
    ]
    quality = max((float(c.score) for c in chunks), default=0.0) if chunks else 0.0
    return success_envelope(
        {"query": query, "snippets": snippets},
        quality_score=quality,
    )


async def handle_httpbin_delay(arguments: dict[str, Any]) -> str:
    seconds = arguments.get("seconds", 5)
    if not isinstance(seconds, (int, float)):
        raise ValueError("seconds 须为数字")
    delay = max(1, min(int(seconds), 30))
    url = f"https://httpbin.org/delay/{delay}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(delay + 5.0)) as client:
        r = await client.get(url)
        r.raise_for_status()
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:500]
    return success_envelope(
        {"seconds": delay, "status": r.status_code, "body": body},
        quality_score=1.0,
    )


async def handle_search_web_stub(arguments: dict[str, Any]) -> str:
    """Decoy：演示 Tool-RAG 应优先 get_kb_snippet 而非网页搜索。"""
    query = arguments.get("query", "")
    return success_envelope(
        {"stub": True, "tool": "search_web_stub", "query": query, "results": []},
        quality_score=0.2,
    )


async def handle_math_llm_stub(arguments: dict[str, Any]) -> str:
    """Decoy：演示路由应优先 calc 而非 LLM 估算。"""
    problem = arguments.get("problem", "")
    return success_envelope(
        {"stub": True, "tool": "math_llm_stub", "problem": problem, "estimate": None},
        quality_score=0.2,
    )

