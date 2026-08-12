from __future__ import annotations

import json
import logging
import re
from typing import Any

from packages.agent.computer_use.models import ActionResult, ScreenState

logger = logging.getLogger("ai_platform.agent.computer_use.planner")

_PLANNER_SYSTEM_PROMPT = (
    "你是计算机操作助手。你需要分析当前屏幕截图，决定下一步操作来完成任务。\n"
    "可用的动作类型：\n"
    '- click(x, y): 点击坐标 (x, y)，x 和 y 的范围 0-1000（归一化坐标）\n'
    '- type(text): 输入文本\n'
    '- key(key): 按键（如 "enter", "tab", "escape", "backspace"）\n'
    '- scroll(dx, dy): 滚动（dy 正数向上，负数向下）\n'
    '- move(x, y): 移动鼠标到坐标\n'
    '- screenshot: 再次截图（用于验证）\n'
    '- done(answer): 任务完成，输出最终答案\n\n'
    "只输出 JSON，不要其他文字。\n"
    '格式：{"action": "click|type|key|scroll|move|screenshot|done", "x": 100, "y": 200, "text": "...", "key": "enter", "dx": 0, "dy": -100, "reasoning": "为什么这样做"}\n'
    "注意：坐标使用归一化范围 0-1000，执行器会自动映射到实际屏幕分辨率。"
)

_PLANNER_USER_TEMPLATE = (
    "任务：{task}\n\n"
    "已完成的操作历史：\n{history}\n\n"
    "请分析当前屏幕截图（base64），决定下一步操作。"
)


class ComputerUsePlanner:
    """Computer Use 截图分析 + 动作规划。

    接收当前截图和任务描述，调用 LLM 决定下一步动作。
    """

    def __init__(self, model: str | None = None):
        self._model = model

    async def plan(
        self,
        screen: ScreenState,
        task: str,
        history: list[ActionResult],
    ) -> ActionResult:
        """分析截图，规划下一步动作。"""
        history_text = self._format_history(history)
        user_prompt = _PLANNER_USER_TEMPLATE.format(
            task=task.strip(),
            history=history_text or "无",
        )

        content = await self._call_llm_with_image(
            system_prompt=_PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            screenshot_base64=screen.screenshot_base64,
        )

        if content is None:
            logger.warning("planner: LLM call failed, taking screenshot")
            return ActionResult(action_type="screenshot", description="LLM 失败，重新截图")

        action = self._parse_action(content)
        if action is None:
            logger.warning("planner: failed to parse action, taking screenshot")
            return ActionResult(action_type="screenshot", description="解析失败，重新截图")

        return action

    async def _call_llm_with_image(
        self,
        system_prompt: str,
        user_prompt: str,
        screenshot_base64: str,
    ) -> str | None:
        """调用支持图片输入的 LLM。"""
        from packages.platform import forward_with_model_router

        # 构建多模态消息
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_base64}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 500,
        }

        try:
            route = await forward_with_model_router(payload)
            if route.status != 200 or not route.body:
                logger.warning("planner: upstream error status=%d", route.status)
                return None
            choices = route.body.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content") or ""
        except Exception as exc:
            logger.warning("planner: LLM call failed: %s", exc)
            return None

    def _parse_action(self, content: str) -> ActionResult | None:
        """从 LLM 输出解析动作。"""
        raw = content.strip()
        if not raw:
            return None

        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence:
            raw = fence.group(1).strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        action_type = str(data.get("action", "screenshot")).strip().lower()
        reasoning = str(data.get("reasoning") or data.get("reasoning") or "")

        return ActionResult(
            action_type=action_type,
            description=reasoning,
            x=self._safe_int(data.get("x")),
            y=self._safe_int(data.get("y")),
            text=str(data.get("text") or "") if data.get("text") else None,
            key=str(data.get("key") or "").lower() if data.get("key") else None,
            dx=self._safe_int(data.get("dx")),
            dy=self._safe_int(data.get("dy")),
            llm_reasoning=reasoning,
        )

    def _safe_int(self, val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _format_history(history: list[ActionResult]) -> str:
        if not history:
            return ""
        lines: list[str] = []
        for i, step in enumerate(history):
            desc = f"{step.action_type}"
            if step.x is not None and step.y is not None:
                desc += f"({step.x}, {step.y})"
            elif step.text:
                desc += f"({step.text[:30]})"
            elif step.key:
                desc += f"({step.key})"
            if step.error:
                desc += f" [error: {step.error}]"
            lines.append(f"  {i + 1}. {desc}")
        return "\n".join(lines)
