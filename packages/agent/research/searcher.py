from __future__ import annotations

import logging
from typing import Any

from packages.agent.research.models import ResearchConfig, ResearchNote

logger = logging.getLogger("ai_platform.agent.research.searcher")


class ResearchSearcher:
    """搜索+阅读+摘要循环。

    对每个子问题：
      1. web_search(sub_q) → 获取结果列表
      2. 对每个结果 fetch_url → 获取正文
      3. LLM 摘要 → ResearchNote
    """

    def __init__(self, model: str | None = None):
        self._model = model

    async def search_and_read(
        self,
        sub_question: str,
        config: ResearchConfig,
        top_k: int = 5,
    ) -> list[ResearchNote]:
        """对一个子问题执行搜索、阅读、摘要。"""
        _ = config
        notes: list[ResearchNote] = []

        # Step 1: 搜索
        search_results = await self._web_search(sub_question, top_k)
        if not search_results:
            logger.info("searcher: no results for %s", sub_question[:40])
            return notes

        # Step 2: 对每个结果阅读
        for result in search_results:
            url = result.get("url", "") or result.get("link", "")
            title = result.get("title", "") or result.get("name", "")
            snippet = result.get("snippet", "") or result.get("description", "")

            if not url:
                continue

            # 获取网页正文
            content = await self._fetch_url(url)
            if not content or content.get("error"):
                # 用 snippet 作为替代
                text_to_summarize = snippet or ""
            else:
                text_to_summarize = content.get("content", snippet)

            if not text_to_summarize or len(text_to_summarize.strip()) < 30:
                continue

            # LLM 摘要
            summary, key_points = await self._summarize(text_to_summarize, sub_question)

            # 多模态截图分析（#201）
            screenshot_analysis, visual_data = await self._capture_and_analyze(url, sub_question)

            notes.append(ResearchNote(
                sub_question=sub_question,
                source_url=url,
                source_title=title,
                summary=summary,
                key_points=key_points,
                screenshot_analysis=screenshot_analysis,
                visual_data=visual_data,
            ))

            if len(notes) >= top_k:
                break

        logger.info(
            "searcher: %s → %d notes",
            sub_question[:40],
            len(notes),
        )
        return notes

    async def _web_search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        """调用 web_search 工具。"""
        from packages.agent.tool_envelope import parse_tool_result
        from packages.agent.tools.web_search import handle_web_search

        try:
            raw = await handle_web_search({"query": query, "top_k": top_k})
            env = parse_tool_result(raw)
            if env.ok and env.data:
                results = env.data.get("results") or []
                return results[:top_k] if isinstance(results, list) else []
        except Exception as exc:
            logger.warning("searcher: web_search failed: %s", exc)
        return []

    async def _fetch_url(self, url: str) -> dict[str, Any]:
        """调用 fetch_url 工具。"""
        from packages.agent.tools.fetch_url import fetch_url_content

        try:
            return await fetch_url_content(url)
        except Exception as exc:
            logger.warning("searcher: fetch_url failed for %s: %s", url, exc)
            return {"error": str(exc)}

    async def _capture_and_analyze(
        self,
        url: str,
        sub_question: str,
    ) -> tuple[str | None, dict | None]:
        """对 URL 进行截图并调用多模态 LLM 分析。

        返回 (screenshot_analysis, visual_data)。
        在 mock 或无头环境下静默降级。
        """
        try:
            from packages.agent.computer_use.executor import ComputerUseExecutor

            executor = ComputerUseExecutor()
            img_bytes = await executor.screenshot()
            if not img_bytes or img_bytes == b"MOCK_SCREENSHOT":
                return None, None

            import base64

            b64 = base64.b64encode(img_bytes).decode()
        except Exception as exc:
            logger.debug("searcher: screenshot capture failed: %s", exc)
            return None, None

        # 调用多模态 LLM
        from packages.platform import forward_with_model_router

        system_prompt = (
            "你是图表分析助手。分析截图中包含的图表、图片或可视化数据，"
            "提取其中的关键信息和数据。"
            "输出 JSON 格式：{\"analysis\": \"...\", \"data\": {\"key\": \"value\"}}\n"
            "analysis 是 2-3 句话的描述，data 是提取的结构化数据。"
        )
        user_content: list[dict] = [
            {"type": "text", "text": f"问题：{sub_question}\nURL：{url}\n请分析截图中的视觉信息。"},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64}",
                    "detail": "high",
                },
            },
        ]

        try:
            route = await forward_with_model_router({
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.2,
                "max_tokens": 1000,
            })
            if route.status == 200 and route.body:
                choices = route.body.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""
                    import json
                    import re
                    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
                    if fence:
                        content = fence.group(1).strip()
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict):
                            analysis = str(data.get("analysis") or "")
                            vis = data.get("data") or None
                            if isinstance(vis, dict):
                                return analysis or None, vis
                            return analysis or None, None
                    except json.JSONDecodeError:
                        pass
                    return content[:500], None
        except Exception as exc:
            logger.debug("searcher: multimodal analysis failed: %s", exc)

        return None, None

    async def _summarize(
        self,
        text: str,
        sub_question: str,
    ) -> tuple[str, list[str]]:
        """调用 LLM 对文本进行摘要。"""
        from packages.platform import forward_with_model_router

        # 截断过长文本
        max_chars = 8000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]"

        system_prompt = (
            "你是研究助手。请阅读以下文本，提取与问题相关的关键信息。"
            "输出 JSON 格式：{\"summary\": \"...\", \"key_points\": [\"...\", ...]}\n"
            "summary 是 2-3 句话的概括，key_points 是 2-5 个核心要点。"
        )
        user_prompt = f"问题：{sub_question}\n\n文本：\n{text}"
        try:
            route = await forward_with_model_router({
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            })
            if route.status == 200 and route.body:
                choices = route.body.get("choices") or []
                if choices:
                    content = (choices[0].get("message") or {}).get("content") or ""
                    import json
                    import re
                    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
                    if fence:
                        content = fence.group(1).strip()
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict):
                            summary = str(data.get("summary") or data.get("summary") or "")
                            kp = data.get("key_points") or data.get("key_points") or []
                            if isinstance(kp, list):
                                return summary, [str(p) for p in kp if p]
                    except json.JSONDecodeError:
                        pass
                    # 回退：取前 500 字符
                    return content[:500], []
        except Exception as exc:
            logger.warning("searcher: summarize failed: %s", exc)

        return "", []
