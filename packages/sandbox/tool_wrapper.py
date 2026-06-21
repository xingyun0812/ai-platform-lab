"""工具包装器 — Phase I #41

将工具调用路由到沙箱执行层。
工具标记 requires_sandbox=True 时会通过 execute_tool_in_sandbox 执行，
否则回退到直接调用（mock 或注册的工具函数）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute_tool_in_sandbox(
    tool_name: str,
    arguments: dict[str, Any],
    config: "Any",  # SandboxConfig, 避免循环导入
) -> str:
    """在沙箱中执行工具调用。

    如果 config.enabled 为 False，则直接模拟调用并返回 JSON 字符串结果。
    否则，将工具名和参数序列化后交由 SandboxExecutor.execute 执行。

    集成点：
    - Agent Registry 中标注 requires_sandbox=True 的工具调用此函数
    - 工具执行结果通过 stdout JSON 解析返回
    """
    from packages.sandbox.executor import get_sandbox_executor

    if not config.enabled:
        logger.debug("sandbox: disabled, calling tool %s directly (mock)", tool_name)
        return json.dumps(
            {"tool": tool_name, "arguments": arguments, "result": "sandbox_disabled"}
        )

    executor = get_sandbox_executor()
    if executor is None:
        logger.warning("sandbox: executor not initialized, calling tool %s directly", tool_name)
        return json.dumps(
            {"tool": tool_name, "arguments": arguments, "result": "executor_not_initialized"}
        )

    # 构建沙箱命令：python3 -c "import json, sys; ..."
    # 实际生产中应将工具代码注入容器镜像；这里仅做 echo 示意
    tool_input_json = json.dumps({"tool": tool_name, "arguments": arguments})
    command = [
        "python3",
        "-c",
        (
            "import json, sys\n"
            f"data = {tool_input_json!r}\n"
            "print(json.dumps({'tool': data['tool'], 'result': 'executed', 'arguments': data['arguments']}))\n"
        ),
    ]

    result = await executor.execute(command, config)

    if result.exit_code == 0 and result.stdout.strip():
        return result.stdout.strip()
    else:
        return json.dumps(
            {
                "tool": tool_name,
                "error": result.stderr or "execution failed",
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
        )
