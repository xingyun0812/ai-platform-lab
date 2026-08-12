from __future__ import annotations

import json
import logging
from typing import Any

from packages.agent.tot.tree import CandidateThought, TotConfig
from packages.platform import forward_with_model_router

logger = logging.getLogger("ai_platform.agent.tot.generator")

_GENERATOR_SYSTEM_PROMPT = (
    "你是思维生成助手。给定一个推理问题与当前推理状态，"
    "生成 {n_candidates} 个不同方向的后续推理步骤。"
    "每个推理步骤应当简洁、有实质内容。"
    "只输出 JSON 数组，不要其他文字。"
    '格式：[{{"thought": "..."}}, ...]'
)

_GENERATOR_USER_TEMPLATE = """
问题：{goal}

当前推理状态：
{state}

请生成 {n_candidates} 个不同的后续推理步骤，每个步骤推进问题的解答。
"""


class ThoughtGenerator:
    """候选思维生成器。

    给定一个推理问题的 goal 和当前的推理 state，
    调用 LLM 生成 N 个不同的候选后继思维。
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float = 0.7,
    ):
        self._model = model
        self._temperature = temperature

    async def generate(
        self,
        state: str,
        goal: str,
        n_candidates: int,
        config: TotConfig | None = None,
    ) -> list[CandidateThought]:
        """生成 N 个候选思维。"""
        temp = config.temperature if config else self._temperature
        user_prompt = _GENERATOR_USER_TEMPLATE.format(
            goal=goal.strip(),
            state=state.strip(),
            n_candidates=n_candidates,
        )
        system_prompt = _GENERATOR_SYSTEM_PROMPT.format(n_candidates=n_candidates)

        content = await self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temp,
        )
        if content is None:
            return []

        candidates = self._parse_candidates(content)
        if not candidates:
            logger.warning("generator: failed to parse candidates from LLM output")
            return []

        return [
            CandidateThought(text=c.strip() if c else "")
            for c in candidates
            if c and c.strip()
        ]

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> str | None:
        """调用 upstream LLM；失败返回 None（降级）。"""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        try:
            route = await forward_with_model_router(payload)
            if route.status != 200 or not route.body:
                logger.warning(
                    "generator: upstream error status=%d", route.status
                )
                return None
            choices = route.body.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content") or ""
        except Exception as exc:
            logger.warning("generator: LLM call failed: %s", exc)
            return None

    def _parse_candidates(self, content: str) -> list[str]:
        """从 LLM 输出中解析候选思维列表。"""
        raw = content.strip()
        if not raw:
            return []
        import re

        # 尝试 JSON 解析
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [
                    item.get("thought", "") if isinstance(item, dict) else str(item)
                    for item in data
                ]
            if isinstance(data, dict):
                candidates = data.get("thoughts") or data.get("candidates") or []
                if isinstance(candidates, list):
                    return [
                        c.get("thought", "") if isinstance(c, dict) else str(c)
                        for c in candidates
                    ]
        except json.JSONDecodeError:
            pass

        # 回退：按行分割
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        if len(lines) >= 2:
            return lines
        return [raw]
