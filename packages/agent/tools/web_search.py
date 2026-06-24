"""Agent 工具 web_search — Phase O #91

外部网页检索；默认 mock 模式（CI 无 Key），可切换 HTTP 搜索 API。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from packages.agent.tool_envelope import success_envelope

logger = logging.getLogger("ai_platform.agent.tools.web_search")

_VALID_MODES = frozenset({"mock", "http"})


def _clamp_top_k(value: Any, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


def normalize_search_results(raw: Any, *, top_k: int) -> list[dict[str, str]]:
    """将 HTTP 响应规范为 [{title, snippet, url}, ...]。"""
    items: list[Any]
    if isinstance(raw, dict):
        candidate = raw.get("results") or raw.get("items") or raw.get("data")
        items = candidate if isinstance(candidate, list) else []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        snippet = str(item.get("snippet") or item.get("description") or item.get("body") or "").strip()
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        if not title and not snippet and not url:
            continue
        out.append(
            {
                "title": title or "(no title)",
                "snippet": snippet or "",
                "url": url or "",
            }
        )
        if len(out) >= top_k:
            break
    return out


def mock_web_search(query: str, *, top_k: int) -> list[dict[str, str]]:
    """确定性 mock 结果，便于单测与 demo。"""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    templates = [
        {
            "title": f"Mock: {query} — overview",
            "snippet": f"[mock] Top summary for '{query}'. Source id={digest}.",
            "url": f"https://example.com/search/{digest}/1",
        },
        {
            "title": f"Mock: {query} — guide",
            "snippet": f"[mock] Practical guide related to '{query}'.",
            "url": f"https://example.com/search/{digest}/2",
        },
        {
            "title": f"Mock: {query} — news",
            "snippet": f"[mock] Recent discussion about '{query}'.",
            "url": f"https://example.com/search/{digest}/3",
        },
        {
            "title": f"Mock: {query} — reference",
            "snippet": f"[mock] Reference material for '{query}'.",
            "url": f"https://example.com/search/{digest}/4",
        },
        {
            "title": f"Mock: {query} — forum",
            "snippet": f"[mock] Community thread mentioning '{query}'.",
            "url": f"https://example.com/search/{digest}/5",
        },
    ]
    return templates[:top_k]


async def http_web_search(
    query: str,
    *,
    top_k: int,
    url: str,
    timeout_seconds: float,
) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url, json={"query": query, "top_k": top_k})
        resp.raise_for_status()
        if "json" in resp.headers.get("content-type", ""):
            return normalize_search_results(resp.json(), top_k=top_k)
        return normalize_search_results(json.loads(resp.text), top_k=top_k)


async def handle_web_search(arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)

    from apps.gateway.settings import get_settings

    settings = get_settings()
    top_k = _clamp_top_k(
        arguments.get("top_k"),
        default=settings.web_search_top_k,
        maximum=settings.web_search_max_top_k,
    )
    mode = (settings.web_search_mode or "mock").strip().lower()
    if mode not in _VALID_MODES:
        mode = "mock"

    results: list[dict[str, str]]
    if mode == "http" and (settings.web_search_url or "").strip():
        try:
            results = await http_web_search(
                query.strip(),
                top_k=top_k,
                url=settings.web_search_url.strip(),
                timeout_seconds=settings.web_search_timeout_seconds,
            )
        except Exception as e:
            logger.warning("web_search http failed, fallback mock: %s", e)
            results = mock_web_search(query.strip(), top_k=top_k)
            mode = "mock_fallback"
    else:
        results = mock_web_search(query.strip(), top_k=top_k)

    return success_envelope(
        {
            "tool": "web_search",
            "mode": mode,
            "query": query.strip(),
            "results": results,
        }
    )
