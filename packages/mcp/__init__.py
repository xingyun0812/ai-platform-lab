"""MCP 真实集成 — Phase F #32

替换 packages/agent/mcp_stub.py 的静态 stub 实现，提供真实 MCP 协议支持：

- **StdioTransport**：通过子进程 stdin/stdout 通信（本地 MCP server）
- **HttpTransport**：通过 HTTP/SSE 通信（远程 MCP server）
- **MCPClient**：JSON-RPC 2.0 协议封装（initialize / tools/list / tools/call）
- **MCPRegistry**：MCP server 注册 + 鉴权 + 工具发现
- **ToolBridge**：将 MCP 工具转换为 Agent ToolDefinition

协议参考：https://spec.modelcontextprotocol.io/

设计要点：
- 启动时按 config/mcp_servers.yaml 注册 MCP server
- Agent 工具调用时透明转发到 MCP server
- 失败降级：MCP server 不可达时跳过其工具，不影响其他工具
- 异步：所有 MCP 调用走 async，与 Agent runner 一致
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import uuid
from typing import Any

from packages.mcp.client import MCPClient, MCPClientError, MCPTool
from packages.mcp.registry import (
    MCPServerConfig,
    MCPServerRegistry,
    MCPServerStatus,
    get_mcp_registry,
    init_mcp_registry,
    reset_mcp_registry_for_tests,
)
from packages.mcp.transport import (
    HttpTransport,
    StdioTransport,
    Transport,
    TransportError,
)

logger = logging.getLogger("ai_platform.mcp")


class MCPCallError(Exception):
    """MCP 工具调用错误。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


async def load_mcp_tools(
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """加载所有已注册 MCP server 的工具，转换为 ToolDefinition。

    失败降级：单个 server 失败不影响其他。

    返回 dict[str, ToolDefinition]（延迟导入避免循环依赖）。
    """
    from packages.agent.tools.base import ToolDefinition

    registry = get_mcp_registry()
    if registry is None:
        return {}
    out: dict[str, Any] = {}
    for server_id in registry.list_server_ids():
        config = registry.get_server(server_id)
        if config is None or not config.enabled:
            continue
        try:
            client = MCPClient(config)
            await client.connect(timeout=timeout_seconds)
            try:
                tools = await client.list_tools(timeout=timeout_seconds)
                for tool in tools:
                    td = _mcp_tool_to_definition(server_id, tool, client)
                    out[td.name] = td
                    logger.info(
                        "mcp tool loaded server=%s tool=%s",
                        server_id,
                        td.name,
                    )
            finally:
                # 不关闭 stdio transport（保持子进程 alive）
                pass
        except Exception as e:
            logger.warning(
                "mcp server %s tool load failed: %s",
                server_id,
                e,
            )
            registry.mark_unhealthy(server_id, str(e))
    return out


def _mcp_tool_to_definition(
    server_id: str,
    tool: MCPTool,
    client: MCPClient,
):
    """将 MCPTool 转为 Agent ToolDefinition。

    handler 异步调用 MCP server 的 tools/call。
    """
    from packages.agent.tools.base import ToolDefinition

    tool_name = f"mcp_{server_id}_{tool.name}"

    async def _handler(arguments: dict[str, Any]) -> str:
        try:
            result = await client.call_tool(
                tool_name=tool.name,
                arguments=arguments,
                timeout=30.0,
            )
            # MCP 返回 content list；拼接为字符串
            if isinstance(result, dict):
                content = result.get("content", [])
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                parts.append(str(item.get("text", "")))
                            else:
                                parts.append(json.dumps(item, ensure_ascii=False))
                        else:
                            parts.append(str(item))
                    return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except MCPClientError as e:
            raise MCPCallError(
                "MCP_CALL_FAILED",
                f"MCP 工具 {tool_name} 调用失败: {e.message}",
                detail={"server_id": server_id, "tool": tool.name},
            ) from e

    return ToolDefinition(
        name=tool_name,
        description=f"[MCP:{server_id}] {tool.description}",
        parameters_schema=tool.input_schema
        if isinstance(tool.input_schema, dict)
        else {"type": "object", "properties": {}},
        handler=_handler,
    )


__all__ = [
    "HttpTransport",
    "MCPCallError",
    "MCPClient",
    "MCPClientError",
    "MCPServerConfig",
    "MCPServerRegistry",
    "MCPServerStatus",
    "MCPTool",
    "StdioTransport",
    "Transport",
    "TransportError",
    "get_mcp_registry",
    "init_mcp_registry",
    "load_mcp_tools",
    "reset_mcp_registry_for_tests",
]
