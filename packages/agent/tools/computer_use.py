from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai_platform.agent.tools.computer_use")


async def handle_screenshot(arguments: dict[str, Any]) -> str:
    """截取当前屏幕并返回 base64 编码的截图。"""
    from packages.agent.computer_use.executor import ComputerUseExecutor
    from packages.agent.tool_envelope import success_envelope

    executor = ComputerUseExecutor()
    try:
        img_bytes = await executor.screenshot()
        import base64

        b64 = base64.b64encode(img_bytes).decode()
        return success_envelope({
            "tool": "screenshot",
            "screenshot_base64": b64,
            "width": executor._screen_width,
            "height": executor._screen_height,
        })
    except Exception as exc:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope("SCREENSHOT_ERROR", str(exc))


async def handle_click(arguments: dict[str, Any]) -> str:
    """点击指定坐标。"""
    from packages.agent.computer_use.executor import ComputerUseExecutor
    from packages.agent.computer_use.models import ActionResult
    from packages.agent.tool_envelope import success_envelope

    x = arguments.get("x")
    y = arguments.get("y")
    if x is None or y is None:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope("INVALID_ARGUMENTS", "click 需要 x 和 y 坐标")

    executor = ComputerUseExecutor()
    action = ActionResult(action_type="click", x=int(x), y=int(y))
    result = await executor.execute(action)
    return success_envelope({
        "tool": "click",
        "x": result.x,
        "y": result.y,
        "error": result.error,
    })


async def handle_type(arguments: dict[str, Any]) -> str:
    """输入文本。"""
    from packages.agent.computer_use.executor import ComputerUseExecutor
    from packages.agent.computer_use.models import ActionResult
    from packages.agent.tool_envelope import success_envelope

    text = arguments.get("text", "")
    if not text:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope("INVALID_ARGUMENTS", "type 需要 text")

    executor = ComputerUseExecutor()
    action = ActionResult(action_type="type", text=str(text))
    result = await executor.execute(action)
    return success_envelope({"tool": "type", "text": text, "error": result.error})


async def handle_key(arguments: dict[str, Any]) -> str:
    """按键。"""
    from packages.agent.computer_use.executor import ComputerUseExecutor
    from packages.agent.computer_use.models import ActionResult
    from packages.agent.tool_envelope import success_envelope

    key = arguments.get("key", "")
    if not key:
        from packages.agent.tool_envelope import failure_envelope

        return failure_envelope("INVALID_ARGUMENTS", "key 需要 key 参数")

    executor = ComputerUseExecutor()
    action = ActionResult(action_type="key", key=str(key))
    result = await executor.execute(action)
    return success_envelope({"tool": "key", "key": key, "error": result.error})
