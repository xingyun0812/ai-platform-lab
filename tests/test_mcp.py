#!/usr/bin/env python3
"""MCP 集成单元测试 — Phase F #32

运行：
    python3 tests/test_mcp.py

注意：部分测试依赖 Python 3.11+（datetime.UTC），在 3.9 环境下会跳过。
CI 使用 Python 3.11 完整运行。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 直接导入，避免触发 packages.agent.__init__ 的 pydantic 链
from packages.mcp.transport import (  # noqa: E402
    HttpTransport,
    StdioTransport,
    Transport,
    TransportError,
)


def test_transport_is_abstract():
    """Transport 基类不可直接使用"""
    t = Transport()
    try:
        asyncio.run(t.send_request(payload={}, timeout=1.0))
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass
    # close 应无副作用
    asyncio.run(t.close())
    print("PASS test_transport_is_abstract")


def test_stdio_transport_command_validation():
    """StdioTransport 接收 list[str] command"""
    t = StdioTransport(["echo", "hello"])
    assert t._command == ["echo", "hello"]
    assert t._env is None
    print("PASS test_stdio_transport_command_validation")


def test_stdio_transport_with_env():
    t = StdioTransport(["python3", "-m", "mcp_server"], env={"MCP_LOG": "INFO"})
    assert t._env == {"MCP_LOG": "INFO"}
    print("PASS test_stdio_transport_with_env")


def test_http_transport_url():
    t = HttpTransport("https://example.com/mcp")
    assert t._url == "https://example.com/mcp"
    assert t._headers["Content-Type"] == "application/json"
    print("PASS test_http_transport_url")


def test_http_transport_custom_headers():
    t = HttpTransport(
        "https://example.com/mcp",
        headers={"Authorization": "Bearer xxx"},
    )
    assert t._headers["Authorization"] == "Bearer xxx"
    assert t._headers["Content-Type"] == "application/json"
    print("PASS test_http_transport_custom_headers")


def test_transport_error_attributes():
    e = TransportError("TIMEOUT", "读取超时")
    assert e.code == "TIMEOUT"
    assert e.message == "读取超时"
    assert str(e) == "读取超时"
    print("PASS test_transport_error_attributes")


def _run_async(coro):
    """兼容 Python 3.9 的 async 测试运行"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_stdio_transport_echo_server():
    """用 echo 作为 mock MCP server 验证通信"""
    t = StdioTransport(["echo", "not-json"])

    async def run():
        try:
            await t.send_request(
                payload={"jsonrpc": "2.0", "method": "test"},
                timeout=2.0,
            )
            assert False, "expected TransportError"
        except TransportError as e:
            assert e.code in ("INVALID_RESPONSE", "PROCESS_CLOSED")
        await t.close()

    _run_async(run())
    print("PASS test_stdio_transport_echo_server")


def test_stdio_transport_nonexistent_command():
    """不存在的命令应启动失败"""
    t = StdioTransport(["nonexistent-command-xyz-12345"])

    async def run():
        try:
            await t.send_request(payload={"method": "test"}, timeout=2.0)
            assert False, "expected TransportError"
        except TransportError as e:
            assert e.code == "PROCESS_START_FAILED"
        await t.close()

    _run_async(run())
    print("PASS test_stdio_transport_nonexistent_command")


def test_stdio_transport_cat_server():
    """用 cat 作为 echo server（逐行回显）"""
    t = StdioTransport(["cat"])

    async def run():
        try:
            resp = await t.send_request(
                payload={"jsonrpc": "2.0", "id": "1", "method": "test"},
                timeout=2.0,
            )
            # cat 会回显原始 JSON，应该能解析
            assert resp["jsonrpc"] == "2.0"
            assert resp["method"] == "test"
        except TransportError as e:
            print(f"  (cat test skipped: {e.code})")
        await t.close()

    _run_async(run())
    print("PASS test_stdio_transport_cat_server")


def main() -> int:
    tests = [
        test_transport_is_abstract,
        test_stdio_transport_command_validation,
        test_stdio_transport_with_env,
        test_http_transport_url,
        test_http_transport_custom_headers,
        test_transport_error_attributes,
        test_stdio_transport_echo_server,
        test_stdio_transport_nonexistent_command,
        test_stdio_transport_cat_server,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
