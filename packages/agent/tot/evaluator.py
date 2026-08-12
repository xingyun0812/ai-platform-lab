from __future__ import annotations

import json
import logging
import re
from typing import Any

from packages.agent.tot.tree import CandidateThought, TotConfig
from packages.platform import forward_with_model_router

logger = logging.getLogger("ai_platform.agent.tot.evaluator")

_EVALUATOR_SYSTEM_PROMPT = (
    "你是思维评估助手。给定一个推理问题和一条候选推理步骤，"
    "评估该步骤对解决问题的价值。"
    "输出 JSON 格式：{\"value\": 0.0-1.0, \"status\": \"sure\"/\"maybe\"/\"impossible\", \"reason\": \"...\"}\n"
    "- value: 0.0(无价值) ~ 1.0(非常关键)\n"
    "- status: sure(确定正确) / maybe(尚可) / impossible(不可能或错误)\n"
    "- reason: 简短的理由说明（一句话）"
)

_EVALUATOR_USER_TEMPLATE = """
问题：{goal}

候选推理步骤：
{thought}

请评估这条推理步骤对解决问题的价值。
"""


class ThoughtEvaluator:
    """思维评估器。

    给定一个推理问题的 goal 和一条候选思维，
    调用 LLM 评分（value）并分类（sure/maybe/impossible）。
    支持批量评估和单条评估两种模式。
    """

    def __init__(
        self,
        *,
        model: str | None = None,
    ):
        self._model = model

    async def evaluate(
        self,
        candidates: list[CandidateThought],
        goal: str,
        config: TotConfig | None = None,
    ) -> list[CandidateThought]:
        """批量评估候选思维，返回带评分和分类的 CandidateThought。"""
        _ = config  # reserved for future use (e.g., temperature override)
        if not candidates:
            return []

        results: list[CandidateThought] = []
        for c in candidates:
            evaluated = await self._evaluate_single(c.text, goal)
            if evaluated:
                results.append(evaluated)
            else:
                # 降级：保留原始候选，给默认评分
                c.value = 0.5
                c.status = "maybe"
                results.append(c)

        return results

    async def _evaluate_single(
        self,
        thought: str,
        goal: str,
    ) -> CandidateThought | None:
        """评估单条候选思维。"""
        user_prompt = _EVALUATOR_USER_TEMPLATE.format(
            goal=goal.strip(),
            thought=thought.strip(),
        )

        content = await self._call_llm(
            system_prompt=_EVALUATOR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        if content is None:
            return None

        parsed = self._parse_evaluation(content)
        if parsed is None:
            return None

        return CandidateThought(
            text=thought,
            value=parsed["value"],
            status=parsed["status"],
            metadata={"reason": parsed.get("reason", "")},
        )

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str | None:
        """调用 upstream LLM；失败返回 None（降级）。"""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,  # 评估用低温度
        }
        try:
            route = await forward_with_model_router(payload)
            if route.status != 200 or not route.body:
                logger.warning(
                    "evaluator: upstream error status=%d", route.status
                )
                return None
            choices = route.body.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content") or ""
        except Exception as exc:
            logger.warning("evaluator: LLM call failed: %s", exc)
            return None

    def _parse_evaluation(self, content: str) -> dict[str, Any] | None:
        """从 LLM 输出解析评估结果。"""
        raw = content.strip()
        if not raw:
            return None

        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            value = data.get("value")
            if not isinstance(value, (int, float)):
                value = 0.5
            value = max(0.0, min(1.0, float(value)))
            status_raw = str(data.get("status", "maybe")).lower().strip()
            if status_raw not in ("sure", "maybe", "impossible"):
                status_raw = "maybe"
            return {
                "value": value,
                "status": status_raw,
                "reason": str(data.get("reason", "")),
            }
        except json.JSONDecodeError:
            logger.warning("evaluator: failed to parse JSON: %.100s", raw)
            return None
