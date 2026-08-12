from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger("ai_platform.agent.tools.fetch_url")

_DEFAULT_TIMEOUT = 15.0
_MAX_CONTENT_LENGTH = 10000


async def fetch_url_content(url: str, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """获取 URL 内容并提取正文。

    返回：
        {"content": str, "title": str, "length": int, "took_ms": float}
        失败时返回 {"error": str}
    """
    start = time.time()
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning("fetch_url: HTTP error for %s: %s", url, exc)
        return {"error": f"HTTP error: {exc}"}

    title = _extract_title(html)
    content = _extract_readable(html)
    if not content or len(content) < 50:
        content = _fallback_extract(html)

    content = content[:_MAX_CONTENT_LENGTH]
    elapsed = (time.time() - start) * 1000
    logger.info("fetch_url: %s — %d chars in %.0fms", url, len(content), elapsed)
    return {
        "content": content,
        "title": title or "",
        "length": len(content),
        "took_ms": round(elapsed, 1),
    }


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def _extract_readable(html: str) -> str:
    """尝试用 readability 或 html2text 提取正文。"""
    # 尝试 readability (pip install readability-lxml)
    try:
        import readability

        doc = readability.Document(html)
        summary_html = doc.summary()
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        text = h.handle(summary_html)
        if len(text.strip()) > 100:
            return text.strip()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("readability extraction failed: %s", exc)

    # 回退：html2text 全页
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        text = h.handle(html)
        if len(text.strip()) > 100:
            return text.strip()
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("html2text extraction failed: %s", exc)

    return ""


def _fallback_extract(html: str) -> str:
    """最简回退：去除 HTML 标签。"""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text).strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines[:200])


async def handle_fetch_url(arguments: dict[str, Any]) -> str:
    """fetch_url 工具的 handler（供 Agent 调用）。"""
    url = (arguments.get("url") or "").strip()
    if not url:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope(error_code="INVALID_ARGUMENTS", message="url 不能为空")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = await fetch_url_content(url)
    if "error" in result:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope(error_code="FETCH_ERROR", message=result["error"])

    from packages.agent.tool_envelope import success_envelope

    return success_envelope(
        {
            "tool": "fetch_url",
            "url": url,
            "title": result["title"],
            "content": result["content"],
            "length": result["length"],
            "took_ms": result["took_ms"],
        }
    )
