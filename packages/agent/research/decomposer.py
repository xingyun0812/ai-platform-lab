from __future__ import annotations

import json
import logging
import re
from typing import Any

from packages.agent.research.models import ResearchConfig

logger = logging.getLogger("ai_platform.agent.research.decomposer")

_DECOMPOSER_SYSTEM_PROMPT_TPL = (
    "你是研究问题分解助手。将一个复杂研究问题分解为 {n} 个具体的子问题。"
    "每个子问题应当独立可搜索、不重叠、覆盖研究的不同维度。"
    "只输出 JSON 数组，不要其他文字。"
)

_DECOMPOSER_USER_TEMPLATE = (
    "研究问题：{question}\n\n"
    "请将以上问题分解为 {n} 个具体的子问题，每个子问题可以独立在搜索引擎中查询。"
    '输出格式：[{{"sub_question": "...", "rationale": "..."}}, ...]'
)


class QuestionDecomposer:
    """问题分解器。

    给定一个研究问题，调用 LLM 分解为多个可搜索的子问题。
    """

    def __init__(self, model: str | None = None):
        self._model = model

    async def decompose(
        self,
        question: str,
        config: ResearchConfig | None = None,
    ) -> list[str]:
        """分解问题为子问题列表。"""
        n = config.max_sub_questions if config else 5
        system_prompt = _DECOMPOSER_SYSTEM_PROMPT_TPL.format(n=n)
        user_prompt = _DECOMPOSER_USER_TEMPLATE.format(question=question.strip(), n=n)

        content = await self._call_llm(system_prompt, user_prompt)
        if content is None:
            logger.warning("decomposer: LLM call failed, using question as-is")
            return [question]

        sub_questions = self._parse_sub_questions(content)
        if not sub_questions:
            logger.warning("decomposer: failed to parse, using question as-is")
            return [question]

        logger.info("decomposer: %s → %d sub-questions", question[:40], len(sub_questions))
        return sub_questions

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str | None:
        from packages.platform import forward_with_model_router

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
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
            logger.warning("decomposer: LLM call failed: %s", exc)
            return None

    def _parse_sub_questions(self, content: str) -> list[str]:
        raw = content.strip()
        if not raw:
            return []
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                result: list[str] = []
                for item in data:
                    if isinstance(item, dict):
                        sq = item.get("sub_question") or item.get("question") or ""
                        if sq:
                            result.append(str(sq).strip())
                    elif isinstance(item, str):
                        result.append(item.strip())
                return result
        except json.JSONDecodeError:
            pass
        lines = [line.strip() for line in raw.split("\n") if line.strip() and not line.strip().startswith("[")]
        lines = [line.lstrip("- ")
        for line in lines if line and line not in ("[", "]", ",")]
        if len(lines) >= 2:
            return lines
        return [raw]
