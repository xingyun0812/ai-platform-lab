from __future__ import annotations

import json
from typing import Any

from packages.platform import REPO_ROOT
from packages.agent.tools.base import ToolDefinition


def load_mcp_stub_tools() -> dict[str, ToolDefinition]:
    """从 config/mcp_tools.json 加载 MCP 风格工具（学习用 stub）。"""
    path = REPO_ROOT / "config" / "mcp_tools.json"
    if not path.is_file():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    tools_raw = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools_raw, list):
        return {}

    out: dict[str, ToolDefinition] = {}

    def _make_handler(tool_name: str):
        async def _handler(arguments: dict[str, Any]) -> str:
            return json.dumps({"tool": tool_name, "arguments": arguments}, ensure_ascii=False)

        return _handler

    for item in tools_raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        out[name] = ToolDefinition(
            name=name,
            description=str(item.get("description", "MCP stub tool")),
            parameters_schema=item.get("parameters_schema")
            if isinstance(item.get("parameters_schema"), dict)
            else {"type": "object", "properties": {}},
            handler=_make_handler(name),
        )
    return out
