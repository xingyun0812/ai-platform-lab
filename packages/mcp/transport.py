"""MCP 传输层 — stdio 与 http 双协议。

StdioTransport：
    通过子进程 stdin/stdout 通信，每行一个 JSON-RPC 消息。
    适用于本地 MCP server（如 Python/Node 实现的 server 脚本）。

HttpTransport：
    通过 HTTP POST 发送 JSON-RPC 请求，可选 SSE 流式响应。
    适用于远程 MCP server。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("ai_platform.mcp.transport")


class TransportError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class Transport:
    """传输层抽象。"""

    async def send_request(
        self,
        *,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        """清理资源。"""
        pass


# --------------------------------------------------------------------- #
# Stdio
# --------------------------------------------------------------------- #

class StdioTransport(Transport):
    """通过子进程 stdin/stdout 通信。

    每行一个 JSON-RPC 消息（以 \\n 分隔）。
    """

    def __init__(self, command: list[str], *, env: dict[str, str] | None = None) -> None:
        self._command = command
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._lock: asyncio.Lock | None = None
        self._stderr_buffer: list[str] = []

    def _get_lock(self) -> asyncio.Lock:
        """延迟创建 asyncio.Lock（兼容 Python 3.9 无事件循环场景）。"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._process is None or self._process.returncode is not None:
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**__import__("os").environ, **(self._env or {})},
                )
                # 启动 stderr 监听任务
                asyncio.create_task(self._drain_stderr())
                logger.info(
                    "stdio transport started cmd=%s pid=%s",
                    self._command[0],
                    self._process.pid,
                )
            except Exception as e:
                raise TransportError(
                    "PROCESS_START_FAILED",
                    f"无法启动 MCP server: {e}",
                ) from e
        return self._process

    async def _drain_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                self._stderr_buffer.append(line.decode("utf-8", errors="replace").rstrip())
                if len(self._stderr_buffer) > 100:
                    self._stderr_buffer = self._stderr_buffer[-100:]
        except Exception as e:
            logger.debug("stderr drain stopped: %s", e)

    async def send_request(
        self,
        *,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        async with self._get_lock():
            process = await self._ensure_process()
            if process.stdin is None or process.stdout is None:
                raise TransportError("NO_STREAM", "子进程 stdin/stdout 不可用")
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            process.stdin.write(line.encode("utf-8"))
            await process.stdin.drain()
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            except TimeoutError as e:
                raise TransportError("TIMEOUT", f"读取响应超时 {timeout}s") from e
            if not raw:
                # 进程可能已退出
                stderr_tail = "\n".join(self._stderr_buffer[-5:])
                raise TransportError(
                    "PROCESS_CLOSED",
                    f"MCP server 进程关闭；stderr 尾部:\n{stderr_tail}",
                )
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise TransportError("INVALID_RESPONSE", f"响应非 JSON: {e}") from e

    async def close(self) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None


# --------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------- #

class HttpTransport(Transport):
    """通过 HTTP POST 发送 JSON-RPC。"""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}

    async def send_request(
        self,
        *,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json=payload,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise TransportError(
                            "HTTP_ERROR",
                            f"HTTP {resp.status}: {text[:200]}",
                        )
                    data = await resp.json()
                    if not isinstance(data, dict):
                        raise TransportError(
                            "INVALID_RESPONSE",
                            f"响应非 JSON object: {type(data).__name__}",
                        )
                    return data
        except aiohttp.ClientError as e:
            raise TransportError("NETWORK_ERROR", f"HTTP 网络错误: {e}") from e
        except TimeoutError as e:
            raise TransportError("TIMEOUT", f"HTTP 请求超时 {timeout}s") from e
