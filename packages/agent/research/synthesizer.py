from __future__ import annotations

import logging
from typing import Any

from packages.agent.research.models import ResearchConfig, ResearchNote

logger = logging.getLogger("ai_platform.agent.research.synthesizer")


class ResearchSynthesizer:
    """信息综合器。

    将所有 ResearchNote 综合成结构化研究报告。
    """

    def __init__(self, model: str | None = None):
        self._model = model

    async def synthesize(
        self,
        question: str,
        notes: list[ResearchNote],
        config: ResearchConfig | None = None,
    ) -> tuple[str, list[str]]:
        """综合所有笔记，生成研究报告和关键发现。"""
        _ = config
        if not notes:
            return "未找到相关信息。", []

        # 构建笔记文本
        notes_text_parts: list[str] = []
        for i, note in enumerate(notes):
            if note.error:
                continue
            notes_text_parts.append(
                f"【来源 {i + 1}】{note.source_title}\n"
                f"URL: {note.source_url}\n"
                f"摘要：{note.summary}\n"
                f"要点：{'；'.join(note.key_points[:5])}"
            )

        if not notes_text_parts:
            return "所有来源均获取失败。", []

        notes_text = "\n\n".join(notes_text_parts)

        system_prompt = (
            "你是研究报告撰写助手。请基于以下收集到的信息，"
            "撰写一份结构化研究报告。"
            "报告应当按主题组织、引用来源、客观全面。"
            '输出 JSON 格式：{"report": "...", "key_findings": ["...", ...]}\n'
            "report 使用 Markdown 格式，包含标题、章节、引用。"
        )

        report_raw = await self._call_llm(
            system_prompt=system_prompt,
            user_prompt=(
                f"研究问题：{question}\n\n"
                f"收集到的信息：\n{notes_text}\n\n"
                "请撰写一份结构化研究报告。"
            ),
        )

        if not report_raw:
            # 回退：简单拼接
            fallback = [f"# {question}\n\n"]
            for i, note in enumerate(notes):
                if note.error:
                    continue
                fallback.append(f"## {note.source_title}\n\n{note.summary}\n")
            return "\n\n".join(fallback), []

        import json
        import re

        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", report_raw, re.IGNORECASE)
        if fence:
            report_raw = fence.group(1).strip()
        try:
            data = json.loads(report_raw)
            if isinstance(data, dict):
                report = str(data.get("report") or data.get("report") or report_raw)
                findings = data.get("key_findings") or data.get("key_findings") or []
                if isinstance(findings, list):
                    return report, [str(f) for f in findings if f]
        except json.JSONDecodeError:
            pass

        return report_raw, []

    async def identify_gaps(
        self,
        question: str,
        report: str,
        config: ResearchConfig | None = None,
    ) -> list[str]:
        """分析报告，识别信息缺口，返回补充搜索问题。"""
        _ = config
        if not report:
            return []

        system_prompt = (
            "你是研究分析助手。请分析以下研究报告，找出信息缺口——"
            "即报告中未覆盖但对回答原始问题很重要的方面。"
            "输出 JSON 格式的补充搜索问题列表。\n"
            '格式：{"gaps": ["补充问题1", "补充问题2", ...]}\n'
            "最多输出 3 个最关键的缺口。如果没有明显缺口，输出空列表。"
        )
        user_prompt = (
            f"原始研究问题：{question}\n\n"
            f"研究报告：\n{report[:3000]}\n\n"
            "请找出信息缺口，生成补充搜索问题。"
        )

        content = await self._call_llm(system_prompt, user_prompt)
        if not content:
            return []

        import json
        import re

        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
        if fence:
            content = fence.group(1).strip()
        try:
            data = json.loads(content)
            gaps = data.get("gaps") or data.get("questions") or []
            if isinstance(gaps, list):
                return [str(g).strip() for g in gaps if g and str(g).strip()][:3]
        except json.JSONDecodeError:
            pass
        return []

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str | None:
        from packages.platform import forward_with_model_router

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        try:
            route = await forward_with_model_router(payload)
            if route.status != 200 or not route.body:
                return None
            choices = route.body.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content") or ""
        except Exception as exc:
            logger.warning("synthesizer: LLM call failed: %s", exc)
            return None
