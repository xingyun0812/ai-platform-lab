"""packages/agent/computer_use — Computer Use Agent。

让 Agent 能通过「截图 → LLM 分析 → 定位 → 点击/输入」的方式操作 GUI 界面。

用法：
    result = await run_computer_use(
        task="打开计算器并计算 1+1",
        config=ComputerUseConfig(max_steps=10),
    )
    print(result.final_answer)
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from packages.agent.computer_use.executor import ComputerUseExecutor
from packages.agent.computer_use.models import (
    ActionResult,
    ComputerUseConfig,
    ComputerUseResult,
    ScreenState,
)
from packages.agent.computer_use.planner import ComputerUsePlanner

logger = logging.getLogger("ai_platform.agent.computer_use")

__all__ = [
    "run_computer_use",
    "ComputerUseConfig",
    "ComputerUseResult",
]


async def run_computer_use(
    task: str,
    config: ComputerUseConfig | None = None,
    model: str | None = None,
) -> ComputerUseResult:
    """运行 Computer Use Agent 的主入口。"""
    cfg = config or ComputerUseConfig()
    start = time.time()
    trace: list[dict[str, Any]] = []
    steps: list[ActionResult] = []

    try:
        trace.append({"event": "computer_use_start", "task": task[:60], "config": cfg.to_dict()})

        planner = ComputerUsePlanner(model=model)
        executor = ComputerUseExecutor(
            sandbox_mode=cfg.sandbox_mode,
            display=cfg.display,
        )

        for step_num in range(cfg.max_steps):
            if time.time() - start > cfg.timeout_seconds:
                logger.warning("computer_use: timeout at step %d", step_num)
                trace.append({"event": "timeout", "step": step_num})
                break

            # 截图
            screenshot_bytes = await executor.screenshot()
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()

            screen = ScreenState(
                screenshot_base64=screenshot_b64,
                width=executor._screen_width,
                height=executor._screen_height,
                step=step_num,
            )

            # LLM 规划动作
            action = await planner.plan(screen, task, steps)

            # 执行动作
            result = await executor.execute(action)
            steps.append(result)

            trace.append({
                "event": "step",
                "step": step_num,
                "action": action.action_type,
                "error": result.error,
            })

            logger.info(
                "computer_use step %d: %s%s",
                step_num,
                action.action_type,
                f" error={result.error}" if result.error else "",
            )

            # 检查是否完成
            if action.action_type == "done":
                final_answer = action.text or result.description or ""
                elapsed = (time.time() - start) * 1000
                trace.append({"event": "complete", "step": step_num})
                logger.info("computer_use: task completed in %d steps", step_num + 1)
                return ComputerUseResult(
                    task=task,
                    final_answer=final_answer,
                    steps=steps,
                    success=True,
                    execution_time_ms=elapsed,
                    trace=trace,
                )

        elapsed = (time.time() - start) * 1000
        logger.info("computer_use: max steps reached")
        return ComputerUseResult(
            task=task,
            final_answer=None,
            steps=steps,
            success=False,
            execution_time_ms=elapsed,
            trace=trace,
            error=f"达到最大步数 {cfg.max_steps}",
        )

    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.error("computer_use failed: %s", exc)
        trace.append({"event": "error", "error": str(exc)})
        return ComputerUseResult(
            task=task,
            final_answer=None,
            steps=steps,
            success=False,
            execution_time_ms=elapsed,
            trace=trace,
            error=str(exc),
        )
