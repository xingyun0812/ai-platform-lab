from __future__ import annotations

import json
from typing import Any

import httpx

from packages.agent.tool_envelope import success_envelope


async def handle_echo(arguments: dict[str, Any]) -> str:
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        return json.dumps({"error": "text 不能为空"}, ensure_ascii=False)
    return success_envelope({"echo": text.strip()})


BUILTIN_PLUGIN_HANDLERS: dict[str, Any] = {
    "echo": handle_echo,
}


def make_http_handler(
    *,
    url: str,
    method: str = "POST",
    timeout_seconds: float = 10.0,
) -> Any:
    async def _handler(arguments: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.request(method.upper(), url, json=arguments)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                return json.dumps(resp.json(), ensure_ascii=False)
            return resp.text

    return _handler
