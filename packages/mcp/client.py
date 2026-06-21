"""MCP 客户端 — JSON-RPC 2.0 协议封装。

实现 MCP 协议核心方法：
- initialize：握手
- tools/list：列出工具
- tools/call：调用工具

协议：JSON-RPC 2.0 over stdio/http
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from packages.mcp.transport import HttpTransport, StdioTransport, Transport, TransportError

logger = logging.getLogger("ai_platform.mcp.client")

PROTOCOL_VERSION = "2024-11-05"


class MCPClientError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class MCPTool:
    """MCP 工具描述。"""
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPServerInfo:
    name: str
    version: str


class MCPClient:
    """MCP 客户端。

    生命周期：
        client = MCPClient(config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "val"})
        # 不显式 close；transport 持有连接
    """

    def __init__(self, config) -> None:
        # config 是 MCPServerConfig，避免循环引用用 duck typing
        self._config = config
        self._transport: Transport | None = None
        self._initialized = False
        self._server_info: MCPServerInfo | None = None
        self._lock = asyncio.Lock()

    @property
    def server_id(self) -> str:
        return getattr(self._config, "server_id", "unknown")

    def _build_transport(self) -> Transport:
        transport_type = getattr(self._config, "transport", "stdio")
        if transport_type == "stdio":
            command = getattr(self._config, "command", [])
            env = getattr(self._config, "env", None)
            if not command:
                raise MCPClientError(
                    "INVALID_CONFIG",
                    "stdio transport 需要 command",
                )
            return StdioTransport(list(command), env=env)
        if transport_type == "http":
            url = getattr(self._config, "url", "")
            headers = getattr(self._config, "headers", None)
            if not url:
                raise MCPClientError(
                    "INVALID_CONFIG",
                    "http transport 需要 url",
                )
            return HttpTransport(url, headers=headers)
        raise MCPClientError(
            "INVALID_CONFIG",
            f"未知 transport: {transport_type}",
        )

    async def connect(self, *, timeout: float = 5.0) -> None:
        """initialize 握手。"""
        async with self._lock:
            if self._initialized:
                return
            self._transport = self._build_transport()
            payload = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ai-platform-lab",
                        "version": "1.0.0",
                    },
                },
            }
            try:
                resp = await self._transport.send_request(
                    payload=payload, timeout=timeout
                )
            except TransportError as e:
                raise MCPClientError("CONNECT_FAILED", e.message) from e
            if "error" in resp:
                err = resp["error"]
                raise MCPClientError(
                    "INITIALIZE_FAILED",
                    f"initialize 失败: {err.get('message', str(err))}",
                )
            result = resp.get("result") or {}
            server_info = result.get("serverInfo") or {}
            self._server_info = MCPServerInfo(
                name=str(server_info.get("name", "unknown")),
                version=str(server_info.get("version", "0")),
            )
            # 发送 initialized 通知（无 id，无需响应）
            try:
                await self._transport.send_request(
                    payload={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    },
                    timeout=2.0,
                )
            except Exception as e:
                logger.debug("initialized notification failed (ignored): %s", e)
            self._initialized = True
            logger.info(
                "mcp client connected server=%s name=%s version=%s",
                self.server_id,
                self._server_info.name,
                self._server_info.version,
            )

    async def list_tools(self, *, timeout: float = 5.0) -> list[MCPTool]:
        if not self._initialized:
            await self.connect(timeout=timeout)
        assert self._transport is not None
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
        try:
            resp = await self._transport.send_request(
                payload=payload, timeout=timeout
            )
        except TransportError as e:
            raise MCPClientError("LIST_TOOLS_FAILED", e.message) from e
        if "error" in resp:
            err = resp["error"]
            raise MCPClientError(
                "LIST_TOOLS_FAILED",
                f"tools/list 失败: {err.get('message', str(err))}",
            )
        result = resp.get("result") or {}
        tools_raw = result.get("tools", [])
        if not isinstance(tools_raw, list):
            return []
        out: list[MCPTool] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not isinstance(name, str):
                continue
            out.append(
                MCPTool(
                    name=name,
                    description=str(t.get("description", "")),
                    input_schema=t.get("inputSchema")
                    if isinstance(t.get("inputSchema"), dict)
                    else {},
                )
            )
        return out

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if not self._initialized:
            await self.connect(timeout=5.0)
        assert self._transport is not None
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        try:
            resp = await self._transport.send_request(
                payload=payload, timeout=timeout
            )
        except TransportError as e:
            raise MCPClientError("CALL_FAILED", e.message) from e
        if "error" in resp:
            err = resp["error"]
            raise MCPClientError(
                "CALL_FAILED",
                f"tools/call 失败: {err.get('message', str(err))}",
            )
        return resp.get("result") or {}

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
        self._initialized = False
